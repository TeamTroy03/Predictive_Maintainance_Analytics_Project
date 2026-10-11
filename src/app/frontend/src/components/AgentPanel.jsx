import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api/client.js'

// ---------------------------------------------------------------------------
// AI assistant: a panel docked on the right side of the screen.
// Any page can open it with a pre-filled question:   const { ask } = useAgent()
//                                                    ask('Why is PUMP-204B critical?')
// Every question is sent together with the current page context
// (page name + selected asset) so the agent knows what the user is looking at.
// ---------------------------------------------------------------------------

const AgentContext = createContext(null)
export const useAgent = () => useContext(AgentContext)

// Work out what the user is looking at from the URL
function useCurrentContext() {
  const location = useLocation()
  const parts = location.pathname.split('/').filter(Boolean)
  const params = new URLSearchParams(location.search)
  return {
    page: parts[0] || 'fleet',
    asset_id: parts[0] === 'equipment' && parts[1] ? decodeURIComponent(parts[1]) : params.get('asset') || undefined,
    range: params.get('range') || undefined,
  }
}

export function AgentProvider({ children }) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([]) // { role: 'user' | 'assistant', content, sources? }
  const [busy, setBusy] = useState(false)
  const context = useCurrentContext()

  async function ask(question, extraContext = {}) {
    if (!question.trim() || busy) return
    setOpen(true)
    const history = messages.map(({ role, content }) => ({ role, content }))
    setMessages((m) => [...m, { role: 'user', content: question }])
    setBusy(true)
    try {
      const result = await api.askAgent(question, { ...context, ...extraContext }, history)
      setMessages((m) => [...m, { role: 'assistant', content: result.answer, sources: result.sources }])
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', content: `Sorry, the agent is not reachable: ${err.message}`, error: true }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <AgentContext.Provider value={{ open, setOpen, messages, setMessages, busy, ask, context }}>
      {children}
    </AgentContext.Provider>
  )
}

const SUGGESTIONS = {
  fleet: ['Which assets need attention today?', 'Summarise fleet health for the shift handover'],
  alarms: ['Which alarm should I handle first?', 'Are any of these alarms related?'],
  equipment: ['Why is this asset in this state?', 'What maintenance do you recommend?'],
  sensors: ['Explain the anomalies in this time window', 'Which sensor changed first?'],
  maintenance: ['Why are work orders overdue?', 'How can we reduce MTTR?'],
}

export default function AgentPanel() {
  const { open, setOpen, messages, setMessages, busy, ask, context } = useAgent()
  const [input, setInput] = useState('')
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  if (!open) return null

  function submit(e) {
    e.preventDefault()
    ask(input)
    setInput('')
  }

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-line bg-white">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <div>
          <div className="text-sm font-semibold">✦ AI Diagnostic Assistant</div>
          <div className="mt-0.5 text-xs text-gray-500">
            Context: {context.page}
            {context.asset_id && <> · <span className="font-medium text-brand">{context.asset_id}</span></>}
          </div>
        </div>
        <div className="flex gap-1">
          {messages.length > 0 && (
            <button onClick={() => setMessages([])} className="rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-100">
              Clear
            </button>
          )}
          <button onClick={() => setOpen(false)} className="rounded px-2 py-1 text-gray-500 hover:bg-gray-100" aria-label="Close">
            ✕
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="space-y-2">
            <p className="text-sm text-gray-500">Ask about anomalies, failures, or maintenance. Try:</p>
            {(SUGGESTIONS[context.page] ?? SUGGESTIONS.fleet).map((s) => (
              <button key={s} onClick={() => ask(s)} className="block w-full rounded-md border border-line px-3 py-2 text-left text-sm hover:border-brand hover:bg-brand-light/40">
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : ''}>
            <div
              className={`max-w-[90%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                m.role === 'user' ? 'bg-brand text-white' : m.error ? 'bg-crit/10 text-crit' : 'bg-canvas text-gray-800'
              }`}
            >
              {m.content}
              {m.sources?.length > 0 && (
                <div className="mt-2 border-t border-line pt-1.5 text-[11px] text-gray-500">Sources: {m.sources.join(', ')}</div>
              )}
            </div>
          </div>
        ))}
        {busy && <div className="text-sm text-gray-500">Agent is thinking…</div>}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={submit} className="flex gap-2 border-t border-line p-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask the agent…"
          className="min-w-0 flex-1 rounded-md border border-line px-3 py-2 text-sm outline-none focus:border-brand"
        />
        <button disabled={busy || !input.trim()} className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
          Send
        </button>
      </form>
    </aside>
  )
}
