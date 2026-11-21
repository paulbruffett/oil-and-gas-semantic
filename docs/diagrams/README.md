# Diagrams

`canonical-data-model.drawio` is the source; edit it in draw.io. Re-export the SVG with a fixed light
theme (it sits on a white figure in the primer, so an adaptive dark rendering would be unreadable) and
drop the DOCTYPE line (the artifact host rejects DTDs in supporting files):

```bash
drawio -x -f svg --theme light -b 16 -o canonical-data-model.svg canonical-data-model.drawio && sed -i '' '/<!DOCTYPE/d' canonical-data-model.svg
```
