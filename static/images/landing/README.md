# ElgoTrack landing page images

Drop real screenshots here. **No template or code changes are needed**: the landing page already points at these file names and
shows a built-in placeholder until a real file exists (a missing file never shows a broken-image icon).

| Folder | File name | Where it appears |
|---|---|---|
| `hero/` | `hero-dashboard.png`, `hero-inventory.png`, `hero-sales.png`, `hero-reconciliation.png`, `hero-reports.png` | Hero slider (in this order) |
| `screenshots/` | `dashboard.png`, `inventory.png`, `sales.png`, `purchases.png`, `customers.png`, `reports.png`, `reconciliation.png`, `invoices.png` | "See it in use" gallery |
| `features/` | *(reserved for future feature illustrations)* | not used yet |
| `.` | `logo.jpeg` (1080x1080 ElgoTrack logo) | landing header/footer, authentication pages, app sidebar fallback |
| `branding/` | `elgotrack-mark.svg` (logo mark), `og-elgotrack.png` (1200x630 social preview) | favicon, link previews |

Guidelines
* Screenshots: **16:10** (for example 1600x1000), PNG or WebP renamed to the `.png` name above only if it really is PNG; for WebP edit the file name in `templates/landing/_hero.html` / `_screens.html`.
* Keep each file under about 250 KB (run through Squoosh, TinyPNG or `cwebp`). Capture the app at a 1440px-wide window; the image is cropped from the top-left.
* Use the demo data (`python manage.py seed_demo`) so no real customer information appears in public screenshots.
* After adding files run `python manage.py collectstatic` in production.
