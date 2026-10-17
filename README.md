# Etch

The static web app deployed at https://etch-app-theta.vercel.app.

Say what circuit board you want and Etch designs it with real KiCad parts, then routes and checks it.

- `index.html`: page markup and styles
- `app.js`: voice and chat client
- `viewer3d.js`: 3D board viewer (`so101.glb`)
- `config.json`: URL of the backend API the page talks to

Deploy: `vercel deploy --prod` from this folder (Vercel project `etch-app`).
