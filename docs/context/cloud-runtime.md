# Cloud Runtime Facts

## Provided Hosting

The competition provides Yandex-hosted servers for team solutions.

Known server parameters from the PDF:

- 4 vCPU
- 16 GB RAM
- 10 GB disk

## Deployment Requirements

The PDF says deployment requires a `Dockerfile` in the project root.

The `platform-admin` repository provides centralized GitLab CI and Helm deployment.

Known team bot chart path:

```text
charts/team-bot
```

Known centralized CI variables from `platform-admin/.gitlab-ci.yml`:

- `DOCKERFILE_PATH`
- `ENABLE_OPS_BUTTONS`
- `PLATFORM_CI_PROJECT_PATH`
- `PLATFORM_CI_REF`
- `PLATFORM_CHART_PATH`
- `TEAM_NAMESPACE`
- `TEAM_RELEASE`
- `RUNTIME_STATE_CONFIGMAP`

## Kubernetes Deployment Facts

The platform chart deploys one container named `bot`.

Default resources from `charts/team-bot/values.yaml`:

- requests:
  - CPU: `100m`
  - memory: `256Mi`
- limits:
  - CPU: `500m`
  - memory: `512Mi`

The pod is scheduled only on nodes with:

```yaml
workload: teams
```

The Kubernetes service account token is not mounted into bot pods by default:

```yaml
automountServiceAccountToken: false
```

## Persistent Storage

The chart can mount persistent storage.

Default mount path:

```text
/data
```

The PDF says data inside `/data` persists after redeploys and restarts.

Use `/data` for:

- files;
- state;
- cache;
- local databases;
- restart recovery data.

## Ops Controls

To enable manual ops jobs, run the pipeline with:

```text
ENABLE_OPS_BUTTONS=yes
```

Known manual actions:

- restart bot;
- start bot;
- stop bot;
- attach disk;
- detach disk.

## Monitoring

The PDF mentions a monitoring panel with dashboards for each team.

Known monitoring content:

- application logs;
- container status;
- service runtime information.

