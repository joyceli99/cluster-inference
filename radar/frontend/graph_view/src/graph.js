export const PHASE_COLORS = {
  Running: '#2da44e',
  Pending: '#d4a72c',
  Succeeded: '#6e7f96',
  Failed: '#cf222e',
  Terminating: '#8250df',
  Unknown: '#8c959f',
}

const JOB_COLOR = '#6639ba'
const UNSCHEDULED_COLOR = '#d4a72c'

export function utilizationColor(pct) {
  const clamped = Math.max(0, Math.min(100, pct))
  return `hsl(${120 - clamped * 1.2}, 65%, 45%)`
}

// kind names every node "<cluster>-worker2" etc.; drop that shared prefix on the canvas.
function commonPrefix(graph) {
  const names = graph.nodes.filter((n) => n.type === 'node' && n.role !== 'unscheduled').map((n) => n.label)
  if (names.length < 2) return ''
  let prefix = names[0]
  for (const name of names) while (!name.startsWith(prefix)) prefix = prefix.slice(0, -1)
  const cut = prefix.lastIndexOf('-') + 1
  return prefix.slice(0, cut)
}

function nodeLabel(n, prefix) {
  if (n.type === 'node') {
    return n.role === 'unscheduled' ? 'unscheduled' : `${n.label.slice(prefix.length)}  ${Math.round(n.utilization)}%`
  }
  if (n.type === 'job') return `${n.label}  ${n.succeeded}/${n.completions ?? '?'}`
  return n.label
}

function nodeColor(n) {
  if (n.type === 'job') return JOB_COLOR
  if (n.type === 'pod') return PHASE_COLORS[n.status] ?? PHASE_COLORS.Unknown
  return n.role === 'unscheduled' ? UNSCHEDULED_COLOR : utilizationColor(n.utilization)
}

export function toElements(graph) {
  const prefix = commonPrefix(graph)
  const running = new Set(graph.nodes.filter((n) => n.type === 'pod' && n.status === 'Running').map((n) => n.id))
  const order = { node: 0, job: 1, pod: 2 }
  const nodes = [...graph.nodes]
    .sort((a, b) => order[a.type] - order[b.type])
    .map((n) => ({ group: 'nodes', data: { ...n, label: nodeLabel(n, prefix), color: nodeColor(n) } }))
  const edges = graph.edges.map((e) => ({ group: 'edges', data: { ...e, active: running.has(e.target) } }))
  return [...nodes, ...edges]
}

const CELL_W = 104
const CELL_H = 72
const SLOT_GAP = 56
const MAX_NODES_PER_ROW = 4

// Deterministic layout: cluster nodes in a grid, each node's pods in a grid inside it,
// Jobs in a row above. Compound (parent) boxes size themselves around their children.
export function computeLayout(graph) {
  const nodes = graph.nodes.filter((n) => n.type === 'node')
  const jobs = graph.nodes.filter((n) => n.type === 'job')
  const podsByNode = {}
  for (const n of graph.nodes) if (n.type === 'pod') (podsByNode[n.parent] ??= []).push(n)

  const dims = nodes.map((n) => {
    const count = podsByNode[n.id]?.length ?? 0
    const cols = Math.max(1, Math.ceil(Math.sqrt(count)))
    return { cols, rows: Math.max(1, Math.ceil(count / cols)) }
  })
  const slotW = Math.max(1, ...dims.map((d) => d.cols)) * CELL_W + SLOT_GAP
  const slotH = Math.max(1, ...dims.map((d) => d.rows)) * CELL_H + SLOT_GAP
  const perRow = Math.max(1, Math.min(nodes.length, MAX_NODES_PER_ROW))

  const positions = {}
  nodes.forEach((n, i) => {
    const cx = (i % perRow) * slotW
    const cy = Math.floor(i / perRow) * slotH
    const pods = podsByNode[n.id] ?? []
    if (!pods.length) positions[n.id] = { x: cx, y: cy }
    const { cols, rows } = dims[i]
    pods.forEach((pod, j) => {
      positions[pod.id] = {
        x: cx + ((j % cols) - (cols - 1) / 2) * CELL_W,
        y: cy + (Math.floor(j / cols) - (rows - 1) / 2) * CELL_H,
      }
    })
  })

  const middle = ((perRow - 1) * slotW) / 2
  jobs.forEach((job, i) => {
    positions[job.id] = { x: middle + (i - (jobs.length - 1) / 2) * CELL_W * 2, y: -slotH * 0.85 }
  })
  return positions
}

export const cytoscapeStyle = [
  {
    selector: 'node[type = "node"]',
    style: {
      shape: 'round-rectangle',
      'background-color': 'data(color)',
      'background-opacity': 0.16,
      'border-width': 3,
      'border-color': 'data(color)',
      label: 'data(label)',
      color: '#e6edf3',
      'font-size': 15,
      'text-valign': 'top',
      'text-halign': 'center',
      'text-margin-y': -6,
      padding: 18,
    },
  },
  { selector: 'node[type = "node"]:childless', style: { width: 84, height: 56 } },
  { selector: 'node[role = "unscheduled"]', style: { 'border-style': 'dashed' } },
  { selector: 'node[type = "node"][!ready][role != "unscheduled"]', style: { 'border-style': 'dotted', 'border-color': '#cf222e' } },
  {
    selector: 'node[type = "pod"]',
    style: {
      shape: 'ellipse',
      width: 22,
      height: 22,
      'background-color': 'data(color)',
      label: 'data(label)',
      color: '#9aa7b4',
      'font-size': 11,
      'text-valign': 'bottom',
      'text-margin-y': 4,
      'text-max-width': 100,
      'text-wrap': 'ellipsis',
    },
  },
  {
    selector: 'node[type = "job"]',
    style: {
      shape: 'diamond',
      width: 34,
      height: 34,
      'background-color': 'data(color)',
      label: 'data(label)',
      color: '#e6edf3',
      'font-size': 13,
      'text-valign': 'top',
      'text-margin-y': -6,
    },
  },
  {
    selector: 'edge',
    style: {
      width: 1.5,
      'line-color': '#57606a',
      'target-arrow-color': '#57606a',
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      'arrow-scale': 0.8,
    },
  },
  {
    selector: 'edge[?active]',
    style: {
      width: 2.5,
      'line-color': '#2da44e',
      'target-arrow-color': '#2da44e',
      'line-style': 'dashed',
      'line-dash-pattern': [8, 5],
    },
  },
  { selector: ':selected', style: { 'overlay-color': '#58a6ff', 'overlay-opacity': 0.25, 'overlay-padding': 6 } },
]
