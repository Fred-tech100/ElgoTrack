# Design notes: charts and colour

The product uses a restrained business palette: white surfaces, deep blue for structure and primary actions, orange as the accent.
Charts follow the same palette and give each colour **one fixed meaning everywhere** so users learn it once.

| Meaning | Colour | Token | Used for |
|---|---|---|---|
| Sales / income | `#1c4a80` | brand-600 | sales bars, best sellers, hourly sales |
| Expenses | `#e8731a` | accent-500 | expense bars (orange is reserved for money going out; never reused for another series) |
| Healthy / paid | `#059669` | emerald-600 | in-stock share, money received figure |
| Low / due soon | `#f59e0b` | amber-500 | low stock, 1–30 days late |
| Out / overdue | `#dc2626` | red-600 | out of stock, over 60 days late |
| Neutral / not yet due | `#94a3b8`, `#7fa9d6` | slate-400, brand-300 | other, credit not yet due |
| Categories | blue, teal, indigo, sky, slate, ochre | | payment methods (cycled in size order) |

Principles
* **Server-rendered SVG/CSS, no chart library and no JavaScript**: fast, works under a strict Content-Security-Policy, prints well.
* One job per chart: trend (grouped bars), composition (donut, stacked bar), ranking (horizontal bars), ageing (bars with a severity ramp).
* Every chart has a numeric legend or value labels, an `aria-label`/`<title>` text alternative, and does not rely on colour alone.
* Empty states say what to do next; finance charts are hidden from roles without `finance.view`.
* Dashboard chart data is cached for 60 seconds; tiles stay live.
* A test asserts the chart colours match the Tailwind design tokens.
