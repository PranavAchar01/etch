# Etch backend on Linux: KiCad 10 (official PPA), ngspice, uv, the engine env, the HTTP API on :3439.
#   docker build -t etch-backend .
#   docker run -p 3439:3439 -e CRUSOE_API_KEY -e OPENAI_API_KEY -e YOU_API_KEY -e ETCH_ACCESS_CODE etch-backend
# Keys come only from the environment at run time; none are baked into the image. See docs/DEPLOY.md.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-managed \
    ETCH_BIND=0.0.0.0 \
    ETCH_PUBLIC_PORT=3439 \
    ETCH_HEAVY_LOCK=/tmp/etch-heavy-lock

# KiCad 10 from the KiCad team's PPA (kicad-cli, pcbnew Python module, symbols, footprints, 3D models)
RUN apt-get update \
 && apt-get install -y --no-install-recommends software-properties-common gpg-agent ca-certificates curl \
 && add-apt-repository -y ppa:kicad/kicad-10.0-releases \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      kicad kicad-symbols kicad-footprints kicad-packages3d \
      ngspice python3 python3-yaml python3-numpy xvfb \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/

RUN useradd -m etch
WORKDIR /app
COPY --chown=etch:etch engine/pyproject.toml engine/uv.lock engine/
USER etch
RUN uv sync --project engine --frozen --no-dev --no-install-project
COPY --chown=etch:etch . .
# warm the per-build env the API starts (uv run --with openai) so the first build does not download it
RUN uv run --quiet --project engine --with openai python -c "import openai, yaml" \
 && kicad-cli version | grep -q '^10\.'

EXPOSE 3439
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fsS http://127.0.0.1:3439/etch/health || exit 1
CMD ["python3", "deploy/etch_public.py"]
