"""Log, register, and deploy the surveillance agent to a Databricks Model Serving endpoint.

Logs agent.py as an MLflow "models from code" pyfunc (with the LLM endpoint + UC-function tools as
resources so on-behalf-of auth works at serving time), registers it to Unity Catalog, and deploys it
via the Databricks Agent Framework. Run with workspace auth on the environment:

    DATABRICKS_CONFIG_PROFILE=adb-... python deploy.py

Idempotent: re-running logs a new model version and updates the endpoint.
"""

from __future__ import annotations

import os

import mlflow
from databricks import agents
from mlflow.models.resources import DatabricksFunction, DatabricksServingEndpoint

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = "oag_fable"
UC_MODEL = f"{CATALOG}.agents.production_agent"
LLM_ENDPOINT = os.environ.get("OAG_AGENT_LLM", "databricks-qwen3-next-80b-a3b-instruct")
EXPERIMENT = os.environ.get("OAG_AGENT_EXPERIMENT", "/Shared/oag_fable_agent")

# Every UC function the agent invokes (as tools) or that those tools call (param), granted for
# on-behalf-of auth at serving time.
KNOWLEDGE_FUNCS = [
    f"{CATALOG}.knowledge.{f}" for f in [
        "param", "resolve_term", "resolve_field", "wells_in_field",
        "surveillance_config", "surveillance_below_expected",
        "deferment_by_cause", "deferment_summary", "decline_wells_faster",
        "welltest_flags", "watchlist_flags",
        "rollup_by_field", "rollup_by_operator", "rollup_by_facility",
    ]
]

INPUT_EXAMPLE = {
    "input": [
        {"role": "user", "content": "Which wells are producing below expected oil rate this week, and by how much?"}
    ]
}


def main() -> None:
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(EXPERIMENT)

    resources = [DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT)]
    resources += [DatabricksFunction(function_name=f) for f in KNOWLEDGE_FUNCS]

    with mlflow.start_run(run_name="surveillance-agent"):
        logged = mlflow.pyfunc.log_model(
            name="agent",
            python_model=os.path.join(HERE, "agent.py"),
            resources=resources,
            input_example=INPUT_EXAMPLE,
            pip_requirements=os.path.join(HERE, "requirements.txt"),
        )
    print("logged:", logged.model_uri)

    mv = mlflow.register_model(logged.model_uri, UC_MODEL)
    print(f"registered: {UC_MODEL} v{mv.version}")

    # Serving-endpoint creation is unavailable on trial workspaces; log+register still succeed there.
    try:
        deployment = agents.deploy(
            UC_MODEL,
            int(mv.version),
            scale_to_zero=True,
            tags={"project": "oag_fable", "layer": "agent"},
        )
        print("deployed endpoint:", getattr(deployment, "endpoint_name", deployment))
        print("query URL:", getattr(deployment, "query_endpoint", "see Serving UI"))
    except Exception as exc:  # noqa: BLE001
        print(f"\nRegistered {UC_MODEL} v{mv.version}, but serving deploy failed:\n  {exc}\n"
              "On a trial workspace this is expected (custom Model Serving is unavailable). Re-run "
              "on a non-trial workspace to create the endpoint; the registered model is usable now via "
              "mlflow.pyfunc.load_model in Databricks compute.")


if __name__ == "__main__":
    main()
