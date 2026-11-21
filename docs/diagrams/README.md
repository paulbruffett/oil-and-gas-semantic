# Diagrams

`canonical-data-model.drawio` is the source; edit it in draw.io. Re-export the SVG with a fixed light
theme (it sits on a white figure in the primer, so an adaptive dark rendering would be unreadable) and
drop the DOCTYPE line (the artifact host rejects DTDs in supporting files):

```bash
drawio -x -f svg --theme light -b 16 -o canonical-data-model.svg canonical-data-model.drawio && sed -i '' '/<!DOCTYPE/d' canonical-data-model.svg
```

The primer notebook embeds the SVG as a cell attachment, because Jupyter serves from `notebooks/` and
can't reach `docs/` by a relative link. After re-exporting, re-embed it (from the repo root);
`tests/test_primer.py` fails until you do:

```bash
python3 -c "import json,base64;p='notebooks/primer.ipynb';nb=json.load(open(p));[c['attachments'].update({'canonical-data-model.svg':{'image/svg+xml':base64.b64encode(open('docs/diagrams/canonical-data-model.svg','rb').read()).decode()}}) for c in nb['cells'] if 'canonical-data-model.svg' in c.get('attachments',{})];open(p,'w').write(json.dumps(nb,indent=1,ensure_ascii=False)+'\n')"
```
