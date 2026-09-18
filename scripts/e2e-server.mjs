import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const python = process.env.PYTHON_EXECUTABLE || resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')
const data = resolve(root, `.cache/browser-${Date.now()}/e2e`)
mkdirSync(data, { recursive: true })
const env = { ...process.env, APP_ENV: 'development', DATABASE_URL: `sqlite:///${resolve(data, 'workspace.db').replaceAll('\\', '/')}`, DATA_DIR: data,
  APP_ORIGIN: 'http://127.0.0.1:5174', MODEL_PROVIDER: 'openai', OPENAI_API_KEY: 'test-only-never-sent', EMBEDDING_PROVIDER: 'local', EMBEDDING_MODEL:'BAAI/bge-small-en-v1.5', EMBEDDING_DIMENSIONS:'384', CHAT_MODEL:'gpt-5.6-luna', GRADER_MODEL:'gpt-5.6-terra', JOB_MODE: 'local', COOKIE_SECURE: 'false',
  BOOTSTRAP_EMAIL: 'admin@example.test', BOOTSTRAP_PASSWORD: 'Browser-test-only-2026!' }
let child
if (process.argv[2] === 'api') {
  const seed = spawnSync(python, ['-m', 'tests.server_app'], { cwd: root, env: {...env,RELAY_E2E_SEED:'1'}, stdio: 'inherit' })
  if (seed.status) process.exit(seed.status)
  child = spawn(python, ['-m', 'uvicorn', 'tests.server_app:app', '--host', '127.0.0.1', '--port', '8001', '--no-access-log'], { cwd: root, env, stdio: 'inherit' })
} else {
  child = spawn(process.execPath, [resolve(root, 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '5174', '--strictPort'],
    { cwd: resolve(root, 'frontend'), env: { ...env, API_PROXY_TARGET: 'http://127.0.0.1:8001' }, stdio: 'inherit' })
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { child.kill(); process.exit(0) })
child.on('exit', code => process.exit(code || 0))
