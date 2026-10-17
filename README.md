# Etch

Next.js landing page for Etch.

## Setup

```bash
git clone https://github.com/PranavAchar01/etch.git
cd etch
./setup.sh
```

The script installs Node 24 (via nvm or fnm if you have one) and pnpm 11.20.0, which is pinned in `package.json`. It installs the exact dependencies from `pnpm-lock.yaml`, then starts the dev server on http://localhost:3000.

Use `./setup.sh --build` to run a production build instead.

Main page: `src/app/page.tsx`.
