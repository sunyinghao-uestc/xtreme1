# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This project is **no longer actively maintained**. The README states the development team has disbanded. The code is available for archival/forking purposes.

## Architecture overview

Xtreme1 is an open-source multimodal data annotation platform. It is a monorepo with two main components:

- **frontend/** — 4 independent Vue 3 + TypeScript + Vite single-page applications
- **backend/** — Spring Boot 2.6.6 Java 11 Maven project using a simplified Clean Architecture

Infrastructure: Docker Compose for local development, Kustomize + Kubernetes for production (via internal GitLab CI). GitHub Actions only mirrors the repo to an internal GitLab instance — no CI runs on GitHub.

## Common commands

### Backend (Java / Spring Boot)

```bash
# Build
cd backend && mvn package

# Run locally (requires MySQL, Redis, MinIO — see docker-compose.yml for ports)
cd backend && java -Dspring.profiles.active=local -jar target/xtreme1-backend-0.9.1-SNAPSHOT.jar

# Run tests
cd backend && mvn test
```

The default `application.yml` uses Docker Compose internal hostnames (`mysql`, `redis`, `minio`). For local development outside Docker, create `backend/src/main/resources/application-local.yml` overriding datasource/redis/minio URLs to `localhost` and the mapped ports (8191, 8192, 8193).

There is one test file at `backend/src/test/java/ai/basic/x1/adapter/TestApplication.java`.

### Frontend (4 independent Vue 3 apps)

Each subdirectory has its own `package.json`. Install dependencies and run each app separately:

```bash
# main admin app (port 3100)
cd frontend/main && yarn install && yarn dev

# point cloud annotation tool (port 3200)
cd frontend/pc-tool && yarn install && yarn dev

# image annotation tool (port 3300)
cd frontend/image-tool && yarn install && yarn dev

# text annotation tool (port 3200)
cd frontend/text-tool && yarn install && yarn dev
```

During dev, Vite proxies `/api` to `http://localhost:8190` (the nginx reverse proxy that routes to backend:8080).

```bash
# Build production bundles (individual apps)
cd frontend/main && yarn build        # outputs to frontend/main/dist

# Lint (main app only — the tool apps have only ESLint)
cd frontend/main && yarn lint:eslint
cd frontend/main && yarn lint:stylelint

# Type check (main app only)
cd frontend/main && yarn type:check

# Run tests (main app only, Jest + ts-jest)
cd frontend/main && yarn test
```

### Docker Compose (full stack)

```bash
docker compose up -d                                    # start all services (pulls images from Docker Hub)
docker compose --profile model up -d                     # include GPU model services
docker compose -f docker-compose.develop.yml build       # build from source instead of pulling images
```

Services and ports: nginx (8190), MySQL (8191), Redis (8192), MinIO S3 (8193) / console (8194), backend (8290), frontend dev (8291).

## Frontend architecture

The 4 apps share the same stack (Vue 3 Composition API, Vite 2, TypeScript, Less, Axios) but differ in complexity:

| App | Purpose | Key extras |
|-----|---------|------------|
| `main` | Full admin dashboard | Pinia state management, vue-router (hash mode), vue-i18n, Ant Design Vue, Windi CSS, Jest tests |
| `pc-tool` | 3D point cloud annotation | vue-router, custom state at `src/state.ts` |
| `image-tool` | 2D image annotation | Minimal — no store, optional router |
| `text-tool` | Text annotation (RLHF) | Mirrors pc-tool structure |

The production Dockerfile builds all 4 apps separately and merges static output into `frontend/dist/`. Nginx routes:
- `/` → main app
- `/tool/pc` → pc-tool
- `/tool/image` → image-tool
- `/tool/text` → text-tool

**API client pattern (main app):** A custom `VAxios` class at `frontend/main/src/utils/http/axios/Axios.ts` wraps Axios with transform hooks, request cancellation, and JWT interceptor. The base URL is set from `VITE_GLOB_API_URL` env variable (defaults to `/api`). Declarative API modules live in `frontend/main/src/api/`.

**API client pattern (tool apps):** Simpler — a raw `axios.create()` instance at `frontend/<tool>/src/api/base.ts` with a hardcoded empty `baseURL`, relying on Vite proxy in dev and relative paths in production.

**State management (main app):** Pinia stores at `frontend/main/src/store/modules/` — `user.ts` (auth/tokens), `permission.ts` (route guards), `app.ts`, `locale.ts`, `multipleTab.ts`, `lock.ts`.

## Backend architecture

Follows a simplified Clean Architecture with three layers:

```
adapter/          Outer layer — controllers, DTOs, DAO implementations, config, filters
  ├── api/controller/     REST controllers (UserController, DatasetController, etc.)
  ├── api/config/         @Configuration classes (SecurityConfig, RedisConfig, MinioConfig, etc.)
  ├── dto/                Request/response DTOs
  └── port/dao/mybatis/   MyBatis-Plus mapper interfaces + entity models (25 mappers, 21 models)
usecase/          Business logic layer (~30 UseCase classes)
entity/           Business objects + enums (70+ BO files, 31 enums)
util/             General-purpose utilities
```

**Key detail:** The README explicitly documents a pragmatic deviation from pure Clean Architecture — the usecase layer calls MyBatis-Plus mapper interfaces directly, rather than going through repository abstractions. This is intentional (they weren't going to swap databases).

**Auth flow:** `JwtAuthenticationFilter` (inserted after `AnonymousAuthenticationFilter` in the Spring Security filter chain) validates JWT tokens on every request. Public endpoints: `/actuator/**`, `/user/register`, `/user/login`, `/ontology/exportAsJson`.

**Database:** MySQL 5.7, accessed via MyBatis-Plus 3.5.0 with logic delete (`isDeleted` field). Migrations are manual SQL scripts at `deploy/mysql/migration/V1__Create_tables.sql` and `V2__Init_data.sql` — no Flyway/Liquibase.

**File storage:** MinIO (S3-compatible) at `deploy/mysql/migration/`.

**Model services:** External ML model containers called over HTTP for image object detection, point cloud detection, point cloud rendering, and image similarity computation.

## Key patterns and conventions

- **Frontend path alias:** `@/` maps to `src/` in each Vue app (configured via Vite `resolve.alias` and tsconfig `paths`).
- **Backend `@LoggedUser` annotation:** Controllers inject the authenticated user via a custom annotation processed by `LoggedUserMethodArgumentResolver`.
- **Backend response format:** `CustomResponseWrapper` wraps all responses in a uniform JSON envelope.
- **Docker Compose override:** `docker-compose.develop.yml` is gitignored; use it for local overrides without modifying the tracked file.
- **Backend code style:** Checkstyle config at `backend/coding-standards/checkstyle.xml`. IntelliJ code format XML at `backend/coding-standards/intellij-code-format.xml`.
