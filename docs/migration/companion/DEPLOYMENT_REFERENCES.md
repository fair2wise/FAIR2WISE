# Deployment implementation references

Owner requirements define the target. These primary-source references support implementation details only; they do not certify the deployed ALS environment. Reviewed 8 October 2026. Use APIs compatible with the project lock, not a silent framework upgrade.

- GitHub, Publishing Docker images: https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images - registry publication, job permissions, build/push and immutable action pinning.
- FastAPI, Behind a proxy: https://fastapi.tiangolo.com/advanced/behind-a-proxy/ - stripped prefixes, ASGI root_path, trusted forwarded headers and OpenAPI URLs.
- Vite, Building for production: https://vite.dev/guide/build - public base, static assets, dynamic URL handling and BASE_URL.
- Vite, Env variables and modes: https://vite.dev/guide/env-and-mode - build-time values and public VITE_ variables; no secrets there.
- Docker, Run multiple processes in a container: https://docs.docker.com/engine/containers/multi-service_container/ - lifecycle responsibility when honoring the requested all-in-one local image.
- Docker, Build secrets: https://docs.docker.com/build/building/secrets/ - do not put credentials in ARG/ENV image layers; scoped build secrets for needed dependency access.
- Docker, Bind mounts: https://docs.docker.com/engine/storage/bind-mounts/ - host-mounted runtime files, explicit access modes and mount lifecycle.

No request was made to the private ALS Tiled endpoints to discover credentials, contents, version or permissions. The URLs and admin-account decision come from the owner, not a connectivity test. No organization-specific Ansible or proxy role was supplied.
