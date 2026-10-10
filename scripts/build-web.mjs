import { mkdir, readFile, writeFile } from 'node:fs/promises';
import openapi from '../web/openapi.mjs';

const html = await readFile(new URL('../ucsb_macros/index.html', import.meta.url), 'utf8');
await mkdir(new URL('../dist/web/', import.meta.url), { recursive: true });
const hosted = html.replace('<html lang="en">', '<html lang="en" data-plan-storage="browser">')
  .replace('</div><a href="https://nutrition.info.dining.ucsb.edu/NetNutrition/1"', '</div><nav style="display:flex;align-items:center;gap:20px"><a href="/docs">API docs</a><a href="https://nutrition.info.dining.ucsb.edu/NetNutrition/1"')
  .replace('</a></header>', '</a></nav></header>');
await writeFile(new URL('../dist/web/index.html', import.meta.url), hosted);
await writeFile(new URL('../dist/web/plans.js', import.meta.url), await readFile(new URL('../ucsb_macros/plans.js', import.meta.url)));
await mkdir(new URL('../dist/web/docs/', import.meta.url), { recursive: true });
await mkdir(new URL('../dist/web/api/', import.meta.url), { recursive: true });
await writeFile(new URL('../dist/web/docs/index.html', import.meta.url), await readFile(new URL('../web/docs.html', import.meta.url)));
await writeFile(new URL('../dist/web/api/openapi.json', import.meta.url), JSON.stringify(openapi,null,2)+'\n');
