"""Turn one saved get_design_context result into a demo frame: component + local assets.

Run:  python _build_frame.py <saved-tool-result.txt> <FrameName>

The generated component is used UNCHANGED apart from its asset constants, which are repointed at files
downloaded here. Figma's asset URLs expire in 7 days and must never be a runtime source; and rewriting
the markup is exactly what would cost the pixel fidelity the demo exists to show.
"""
import json, re, subprocess, sys, pathlib

src, name = sys.argv[1], sys.argv[2]
d = json.load(open(src, encoding='utf-8'))
txt = ''.join(b.get('text', '') for b in d if isinstance(b, dict))
if 'export default function' not in txt:
    print('NO CODE in', src); raise SystemExit(1)
start = txt.index('const img') if 'const img' in txt else txt.index('export default function')
code = txt[start:]
code = code[:code.rindex('}') + 1]

adir = pathlib.Path('assets'); adir.mkdir(exist_ok=True)
pairs = re.findall(r'const (\w+) = "(https://www\.figma\.com/api/mcp/asset/[^"]+)";', code)
ok = bad = 0
for cname, url in pairs:
    ext = '.svg' if url.endswith('.svg') else '.png'
    fn = f'{name}_{cname}{ext}'
    p = adir / fn
    if not p.exists():
        r = subprocess.run(['curl', '-f', '-L', '-s', '-o', str(p), url], capture_output=True)
        if r.returncode != 0 or not p.exists() or p.stat().st_size == 0:
            bad += 1; print('  FAILED', cname); continue
    ok += 1
    code = code.replace(f'const {cname} = "{url}";', f'const {cname} = "./assets/{fn}";')

left = len(re.findall(r'figma\.com/api/mcp/asset', code))
# the component's own name, so main.tsx can import it predictably
code = re.sub(r'export default function \w+\(', f'export default function {name}(', code, count=1)
pathlib.Path('frames').mkdir(exist_ok=True)
out = pathlib.Path('frames') / f'{name}.tsx'
out.write_text(code, encoding='utf-8')
print(f'{name}: {len(code)} chars, assets ok={ok} failed={bad}, remote refs left={left} -> {out}')
