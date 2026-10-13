import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api/client.js'

// ---------------------------------------------------------------------------
// AI assistant: a panel docked on the right side of the screen.
// Any page can open it with a pre-filled question:   const { ask } = useAgent()
//                                                    ask('Why is PUMP-204B critical?')
// Every question is sent with the current page context (page name + selected asset) written in front of it,
// so the agent knows what the user is looking at.
// The agent works in two steps: it first proposes a plan, and only runs it once the user approves.
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

// The backend accepts a question of at most 1000 characters
const MAX_QUESTION = 1000

// randomUUID needs a secure context (https or localhost); the fallback keeps plain-http deployments working
const newConversationId = () => crypto.randomUUID?.() ?? `c${Date.now()}${Math.random().toString(36).slice(2)}`

function withContext(question, context) {
  const parts = Object.entries(context)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k}: ${v}`)
  const prefix = parts.length ? `[Context - ${parts.join(', ')}] ` : ''
  return (prefix + question).slice(0, MAX_QUESTION)
}

// The API error envelope is { error: { code, message } }; request() puts it in err.cause
const errorText = (err) => err.cause?.message ?? err.message

export function AgentProvider({ children }) {
  const [open, setOpen] = useState(false)
  // { role: 'user', content }
  // { role: 'assistant', kind: 'plan', plan, runId, state: 'pending' }   (removed from the list once approved or declined)
  // { role: 'assistant', kind: 'answer', content, result }   (result = AssistantAnswer from the API)
  // { role: 'assistant', kind: 'error', content, error: true }
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  // One memory per page load: the messages above are lost on reload, so the agent's memory starts fresh too
  const conversationId = useRef(newConversationId())
  const context = useCurrentContext()
  const awaitingApproval = messages.some((m) => m.kind === 'plan' && m.state === 'pending')

  const add = (message) => setMessages((m) => [...m, message])
  const addError = (err) =>
    add({ role: 'assistant', kind: 'error', content: `The agent could not answer: ${errorText(err)}`, error: true })
  const answerMessage = (result) => ({ role: 'assistant', kind: 'answer', content: result.answer, result })

  async function ask(question, extraContext = {}) {
    if (!question.trim() || busy || awaitingApproval) return
    setOpen(true)
    add({ role: 'user', content: question })
    setBusy(true)
    try {
      const res = await api.askAgent(withContext(question, { ...context, ...extraContext }), conversationId.current)
      if (res.status === 'awaiting_approval') add({ role: 'assistant', kind: 'plan', plan: res.plan, runId: res.run_id, state: 'pending' })
      else add(answerMessage(res.result))
    } catch (err) {
      addError(err)
    } finally {
      setBusy(false)
    }
  }

  // The user's decision on a plan: approved -> the agent runs it and answers, declined -> nothing runs
  async function decide(runId, approved) {
    if (busy) return
    // The decision removes the plan from the chat; the answer (or error) that follows replaces it
    setMessages((m) => m.filter((x) => x.runId !== runId))
    setBusy(true)
    try {
      const res = await api.approveAgent(runId, approved)
      add(answerMessage(res.result))
    } catch (err) {
      if (err.cause?.code === 'unknown_run') {
        add({ role: 'assistant', kind: 'error', content: 'This plan has expired (the server may have restarted). Please ask the question again.', error: true })
      } else {
        addError(err)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <AgentContext.Provider value={{ open, setOpen, messages, setMessages, busy, awaitingApproval, ask, decide, context }}>
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
  const { open, setOpen, messages, setMessages, busy, awaitingApproval, ask, decide, context } = useAgent()
  const [input, setInput] = useState('')
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  // Grow the input with its text (capped by max-h in the className; it scrolls past that)
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [input, open])

  if (!open) return null

  function submit(e) {
    e.preventDefault()
    ask(input)
    setInput('')
  }

  return (
    <aside className="flex w-[368px] shrink-0 flex-col border-l border-line bg-panel max-md:fixed max-md:inset-0 max-md:z-30 max-md:w-full">
      <div className="flex items-center justify-between gap-3 border-b border-line bg-gray-50 px-4 py-2.5">
        <div className="min-w-0">
          <div className="font-display text-lg font-semibold leading-tight">AI Diagnostic Assistant</div>
          <div className="mt-0.5 truncate text-xs text-gray-500">
            Looking at <span className="font-medium capitalize text-gray-800">{context.page}</span>
            {context.asset_id && <span className="ml-1 rounded-sm bg-brand-light px-1.5 py-0.5 font-medium text-brand">{context.asset_id}</span>}
          </div>
        </div>
        <div className="flex shrink-0 gap-1">
          {messages.length > 0 && (
            <button onClick={() => setMessages([])} className="rounded px-2 py-1 text-xs text-gray-600 hover:bg-gray-200">
              Clear
            </button>
          )}
          <button onClick={() => setOpen(false)} className="rounded px-2 py-1 text-gray-600 hover:bg-gray-200" aria-label="Close assistant">
            ✕
          </button>
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="space-y-2">
            <p className="text-sm text-gray-500">Ask about anomalies, failures or maintenance. For example:</p>
            {(SUGGESTIONS[context.page] ?? SUGGESTIONS.fleet).map((s) => (
              <button key={s} onClick={() => ask(s)} className="block w-full rounded-md border border-line px-3 py-2 text-left text-sm hover:border-brand hover:bg-brand-light/40">
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'user' ? 'flex justify-end' : ''}>
            {m.kind === 'plan' ? (
              <PlanBubble message={m} busy={busy} onDecide={decide} />
            ) : (
              <div
                className={`max-w-[90%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm leading-relaxed ${
                  m.role === 'user'
                    ? 'bg-ink text-white'
                    : m.error
                      ? 'border-l-4 border-crit bg-crit/10 text-crit'
                      : 'border-l-4 border-brand bg-gray-100 text-gray-800'
                }`}
              >
                {m.content}
                {m.result && <AnswerDetails result={m.result} />}
              </div>
            )}
          </div>
        ))}
        {busy && <Working />}
        {awaitingApproval && !busy && <div className="text-xs text-gray-500">Approve or decline the plan to continue.</div>}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={submit} className="flex items-end gap-2 border-t border-line bg-gray-50 p-3">
        <textarea
          ref={inputRef}
          rows={1}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter adds a line
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              e.currentTarget.form.requestSubmit()
            }
          }}
          placeholder="Ask the agent…"
          className="max-h-48 min-w-0 flex-1 resize-none rounded-md border border-line bg-panel px-3 py-2 text-sm outline-none focus:border-brand"
        />
        <button disabled={busy || awaitingApproval || !input.trim()} className="rounded-md bg-brand px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
          Send
        </button>
      </form>
    </aside>
  )
}

// "Agent working ." -> ".." -> "..." on repeat; the dots get a fixed width so the text doesn't jump
function Working() {
  const [dots, setDots] = useState(1)
  useEffect(() => {
    const id = setInterval(() => setDots((d) => (d % 3) + 1), 450)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="text-sm text-gray-500">
      Agent working<span className="inline-block w-4">{'.'.repeat(dots)}</span>
      <div className="text-xs">This can take up to a minute.</div>
    </div>
  )
}

// The agent's plan, shown before anything runs. Only the user's approval lets the agent query the data.
function PlanBubble({ message, busy, onDecide }) {
  const { plan, runId } = message
  // The plan leaves the chat as soon as it is decided, so it only ever shows the waiting state (amber lamp)
  return (
    <div className="flex max-w-[95%] overflow-hidden rounded-lg border border-line bg-panel text-sm text-gray-800">
      <div aria-hidden className="w-1.5 shrink-0 bg-warn" />
      <div className="min-w-0 px-3 py-2">
        <div className="font-display text-base font-semibold">Plan waiting for your approval</div>
        <div className="mt-1 whitespace-pre-wrap leading-relaxed">{plan}</div>
        <p className="mt-2 text-xs text-gray-500">Nothing runs against the data until you approve.</p>
        <div className="mt-2 flex gap-2">
          <button disabled={busy} onClick={() => onDecide(runId, true)} className="rounded-md bg-brand px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40">
            Approve
          </button>
          <button disabled={busy} onClick={() => onDecide(runId, false)} className="rounded-md border border-line px-4 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-40">
            Decline
          </button>
        </div>
      </div>
    </div>
  )
}

// Only the grounding warning is shown; sources, SQL, filters, tools and limitations stay in the API
// response (evidence, source_measure, filters, limitations) and in the agent log, not in the UI.
function AnswerDetails({ result }) {
  if (result.grounded) return null
  return <div className="mt-2 text-xs font-medium text-warn">Not grounded in data: treat this answer with caution.</div>
}
