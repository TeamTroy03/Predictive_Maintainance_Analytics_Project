// Button in the top bar that toggles the AI assistant panel
export default function AskAiButton({ onClick, open }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Open AssetPulse AI Assistant"
      aria-expanded={open}
      className="inline-flex cursor-pointer items-center gap-1.5 rounded-md border border-brand-glow/50 bg-white/5 px-3 py-1.5 text-sm font-medium text-white hover:bg-white/15 aria-expanded:bg-white/20"
    >
      <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" className="h-4 w-4 text-brand-glow">
        <path d="M12 2l1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9l5.2-1.8L12 2z" />
        <path d="M19 14l.9 2.6L22.5 17.5l-2.6.9L19 21l-.9-2.6-2.6-.9 2.6-.9L19 14z" />
        <path d="M5 15l.7 2 2 .7-2 .7L5 20.5l-.7-2-2-.7 2-.7L5 15z" />
      </svg>
      <span className="hidden sm:inline">Ask AI Assistant</span>
    </button>
  )
}