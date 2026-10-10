import { mkdir, readFile, writeFile } from 'node:fs/promises';

const html = await readFile(new URL('../ucsb_macros/index.html', import.meta.url), 'utf8');
await mkdir(new URL('../dist/web/', import.meta.url), { recursive: true });
await writeFile(new URL('../dist/web/index.html', import.meta.url), html.replace('<html lang="en">', '<html lang="en" data-plan-storage="browser">'));
await writeFile(new URL('../dist/web/plans.js', import.meta.url), await readFile(new URL('../ucsb_macros/plans.js', import.meta.url)));
