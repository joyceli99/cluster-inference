import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import { computeLayout, cytoscapeStyle, toElements } from './graph'

// Updates the existing graph in place so colours change without the layout jumping;
// the layout only re-runs when nodes or pods appear, disappear or move.
function applyGraph(cy, graph, firstRender) {
  const elements = toElements(graph)
  const incoming = new Set(elements.map((e) => e.data.id))
  let topologyChanged = false

  cy.batch(() => {
    const stale = cy.elements().filter((el) => !incoming.has(el.id()))
    if (stale.length) {
      cy.remove(stale)
      topologyChanged = true
    }
    const fresh = []
    for (const el of elements) {
      const existing = cy.getElementById(el.data.id)
      if (existing.empty()) {
        fresh.push(el)
        continue
      }
      if (existing.isNode() && (existing.data('parent') ?? null) !== (el.data.parent ?? null)) {
        existing.move({ parent: el.data.parent ?? null })
        topologyChanged = true
      }
      existing.data(el.data)
    }
    if (fresh.length) {
      cy.add(fresh)
      topologyChanged = true
    }
  })

  if (topologyChanged) {
    const positions = computeLayout(graph)
    cy.layout({
      name: 'preset',
      positions: (node) => positions[node.id()],
      fit: true,
      padding: 60,
      animate: !firstRender,
      animationDuration: 350,
    }).run()
  }
}

export default function RadarGraph({ graph, onSelect }) {
  const containerRef = useRef(null)
  const cyRef = useRef(null)
  const renderedOnce = useRef(false)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  useEffect(() => {
    const cy = cytoscape({ container: containerRef.current, style: cytoscapeStyle, wheelSensitivity: 0.25, maxZoom: 1.5 })
    cyRef.current = cy
    cy.on('tap', (evt) => onSelectRef.current(evt.target === cy ? null : evt.target.data()))

    let frame
    let offset = 0
    const tick = () => {
      offset = (offset - 0.5) % 13
      const active = cy.edges('[?active]')
      if (active.length) cy.batch(() => active.style('line-dash-offset', offset))
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(frame)
      cy.destroy()
    }
  }, [])

  useEffect(() => {
    if (!graph || !cyRef.current) return
    applyGraph(cyRef.current, graph, !renderedOnce.current)
    renderedOnce.current = true
  }, [graph])

  return <div ref={containerRef} className="graph" />
}
