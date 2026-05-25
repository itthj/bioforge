import { useState } from "react";

interface ChatInputProps {
  onSubmit: (goal: string) => void;
  disabled?: boolean;
}

export function ChatInput({ onSubmit, disabled }: ChatInputProps) {
  const [text, setText] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setText("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Cmd/Ctrl + Enter submits — mirrors most chat UIs and keeps Enter free for
    // newlines inside multi-line goals.
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit(e as unknown as React.FormEvent);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        rows={3}
        placeholder="What do you want BioForge to do? e.g. 'GC content of the reverse complement of ATGCATGC'"
        className="w-full rounded-md border border-slate-300 bg-white p-3 font-mono text-sm shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 disabled:bg-slate-100"
      />
      <div className="flex items-center justify-between text-xs text-slate-500">
        <span>Ctrl/Cmd + Enter to send</span>
        <button
          type="submit"
          disabled={disabled || !text.trim()}
          className="rounded-md bg-slate-900 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-400"
        >
          {disabled ? "Running…" : "Send"}
        </button>
      </div>
    </form>
  );
}
