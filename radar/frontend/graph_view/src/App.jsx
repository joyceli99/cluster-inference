import { useEffect, useState } from 'react'
import RadarGraph from './RadarGraph'
import { PHASE_COLORS, utilizationColor } from './graph'

const SNAPSHOT = new URLSearchParams(window.location.search).has('snapshot')
const POLL_MS = 2000

async function fetchGraph() {
  const res = await fetch(SNAPSHOT ? '/sample-graph.json' : '/api/graph', { cache: 'no-store' })
  if (!res.ok) {
    let detail = `backend unreachable (HTTP ${res.status}) - is it running on port 8000?`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {}
    throw new Error(detail)
  }
  return res.json()
}

const fmtBytes = (b) => (b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(1)} Gi` : `${Math.round(b / 2 ** 20)} Mi`)

function Details({ item }) {
  if (!item) return <p className="hint">Click a node, pod or job for details.</p>
  const rows =
    item.type === 'node' && item.role !== 'unscheduled'
      ? [
          ['role', item.role],
          ['ready', String(item.ready)],
          ['cpu requested', `${item.cpu.requested} / ${item.cpu.allocatable} (${item.cpu.pct}%)`],
          ['memory requested', `${fmtBytes(item.memory.requested)} / ${fmtBytes(item.memory.allocatable)} (${item.memory.pct}%)`],
          ['pods (incl. system)', item.pod_count],
        ]
      : item.type === 'node'
        ? [['pods waiting', item.pod_count]]
        : item.type === 'pod'
          ? [
              ['namespace', item.namespace],
              ['status', item.status],
              ['reason', item.reason ?? '-'],
              ['placed on', item.parent.replace('node/', '')],
              ['cpu request', item.cpu_request],
              ['memory request', fmtBytes(item.memory_request)],
            ]
          : [
              ['namespace', item.namespace],
              ['parallelism', item.parallelism ?? '-'],
              ['completions', item.completions ?? '-'],
              ['active', item.active],
              ['succeeded', item.succeeded],
              ['failed', item.failed],
            ]
  const title = item.type === 'node' ? item.id.replace('node/', '') : item.id.split('/').slice(1).join('/')
  return (
    <div>
      <h3>
        <span className="kind">{item.type}</span> {title}
      </h3>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export default function App() {
  const [graph, setGraph] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    let timer
    let cancelled = false
    const poll = async () => {
      try {
        const next = await fetchGraph()
        if (!cancelled) {
          setGraph(next)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
      if (!cancelled && !SNAPSHOT) timer = setTimeout(poll, POLL_MS)
    }
    poll()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

  const summary = Object.entries(graph?.summary ?? {})

  return (
    <div className="app">
      <header>
        <h1>Cluster Radar</h1>
        {graph && <span className="cluster">{graph.cluster}</span>}
        <span className={`live ${error ? 'bad' : SNAPSHOT ? 'snap' : 'ok'}`}>
          {error ? 'disconnected' : SNAPSHOT ? 'sample snapshot' : 'live'}
        </span>
        <div className="chips">
          {summary.map(([status, count]) => (
            <span key={status} className="chip" style={{ borderColor: PHASE_COLORS[status] ?? PHASE_COLORS.Unknown }}>
              {count} {status.toLowerCase()}
            </span>
          ))}
        </div>
        {graph && <span className="stamp">{new Date(graph.timestamp).toLocaleTimeString()}</span>}
      </header>
      {error && <div className="banner">Backend error: {error}{graph ? ' (showing last known state)' : ''}</div>}
      <main>
        <RadarGraph graph={graph} onSelect={setSelected} />
        <aside>
          <Details item={selected} />
          <div className="legend">
            <h4>Node utilization (requested / allocatable)</h4>
            <div className="ramp" style={{ background: `linear-gradient(90deg, ${utilizationColor(0)}, ${utilizationColor(50)}, ${utilizationColor(100)})` }} />
            <div className="ramp-labels"><span>0%</span><span>100%</span></div>
            <h4>Pod status</h4>
            {Object.entries(PHASE_COLORS).map(([status, color]) => (
              <div key={status} className="key"><i style={{ background: color }} />{status}</div>
            ))}
            <div className="key"><i className="diamond" />Job</div>
          </div>
        </aside>
      </main>
    </div>
  )
}
