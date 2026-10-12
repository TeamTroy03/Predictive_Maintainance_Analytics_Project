// Pill button in the top bar that toggles the AI assistant panel
export default function AskAiButton({ onClick, open }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Open AssetPulse AI Assistant"
      aria-expanded={open}
      className="group inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-purple-200 bg-purple-50 px-3.5 py-1.5 text-xs font-semibold text-purple-800 shadow-sm transition-all duration-150 ease-in-out hover:border-purple-400 hover:bg-purple-100 hover:text-purple-900 hover:shadow-[0_0_0_3px_rgba(161,0,255,0.15)] active:scale-95 focus:outline-none focus:ring-2 focus:ring-purple-500 focus:ring-offset-1"
    >
      <svg
        viewBox="0 0 24 24"
        fill="currentColor"
        aria-hidden="true"
        className="h-4 w-4 text-[#a100ff] transition-transform duration-150 group-hover:scale-110"
      >
        <path d="M12 2l1.8 5.2L19 9l-5.2 1.8L12 16l-1.8-5.2L5 9l5.2-1.8L12 2z" />
        <path d="M19 14l.9 2.6L22.5 17.5l-2.6.9L19 21l-.9-2.6-2.6-.9 2.6-.9L19 14z" />
        <path d="M5 15l.7 2 2 .7-2 .7L5 20.5l-.7-2-2-.7 2-.7L5 15z" />
      </svg>
      <span>Ask AI Assistant</span>
    </button>
  )
}