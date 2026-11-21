# Diagrams

`canonical-data-model.drawio` is the source; edit it in draw.io. It has two exports, both in a fixed
light theme (they sit on white, so an adaptive dark rendering would be unreadable). Run from this folder:

```bash
drawio -x -f svg --theme light -b 16 -o canonical-data-model.svg canonical-data-model.drawio && sed -i '' '/<!DOCTYPE/d' canonical-data-model.svg
```

```bash
drawio -x -f png --theme light -s 2 -b 16 -o canonical-data-model.png canonical-data-model.drawio
```

- The **SVG** is for `docs/primer.html`. Its DOCTYPE line is dropped because the artifact host rejects
  DTDs in supporting files.
- The **PNG** is embedded in `notebooks/primer.ipynb` as a cell attachment. Jupyter serves from
  `notebooks/` and can't reach `docs/` by a relative link, and it strips SVG images from untrusted
  notebooks. After re-exporting, re-embed it from the repo root; `tests/test_primer.py` fails until you do:

```bash
python3 -c "import json,base64;p='notebooks/primer.ipynb';nb=json.load(open(p));[c['attachments'].update({'canonical-data-model.png':{'image/png':base64.b64encode(open('docs/diagrams/canonical-data-model.png','rb').read()).decode()}}) for c in nb['cells'] if 'canonical-data-model.png' in c.get('attachments',{})];open(p,'w').write(json.dumps(nb,indent=1,ensure_ascii=False)+'\n')"
```
