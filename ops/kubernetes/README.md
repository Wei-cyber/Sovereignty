# Kubernetes operations

Read the [main README](../../README.md) first. Google setup is in [Google connections](../../docs/GOOGLE_CONNECTIONS.md).

Local cluster: `relay-local`; context: `kind-relay-local`; namespace: `relay`; kubeconfig: `.cache/kubeconfig-relay`; browser: **http://localhost:8088**. Never use an unverified default context.

Nginx serves React. The backend contains API/worker/scheduler containers sharing `/data`. PostgreSQL/pgvector and Redis have persistent claims. Gmail MCP, Drive MCP and search use private services, health probes and resource limits. Generate checked-in manifests with `uv run python -m scripts.kubernetes_manifests`; credentials are Secret references.

Upgrade with `uv run python -m scripts.deploy_kubernetes --upgrade`. The script preserves CHAT_MODEL, sets the Terra grader, snapshots data/configuration/secrets, stops application workers for migration and reconciles workloads. Failed migration must be investigated before restarting workers; never reset storage as a repair shortcut.

Backups: `.cache/kubernetes-backups/TIMESTAMP/{before-v4.dump,documents.tar.gz,configuration.json,secrets-private.json}`. Copy to approved encrypted storage. Restore into an isolated PostgreSQL instance and verify content/counts. `pg_restore --list` checks archive structure; an actual isolated restore is needed to verify recoverability. Preserve and back up the connection encryption key, including newly generated secrets after upgrade.

Single-node kind is not highly available. Enterprise rollout requires HTTPS/secure cookies, a policy-enforcing CNI, private service networking, appropriate storage classes, secret controls and capacity tests. Deleting kind destroys local storage; stopping Docker temporarily stops Relay.
