import { useState } from "react";

interface ChatInputProps {
  onSubmit: (goal: string) => void;
  disabled?: boolean;
}

// Pre-filled goal templates exposed as quick-action chips above the textarea. Picked
// to demonstrate the agent's range: a trivial single-tool goal, a two-tool chain, the
// CRISPR composite workflow, and a refusal-shaped goal so users can see how the agent
// handles capability gaps. Clicking a chip just pre-fills the textarea — the user can
// edit before sending.
const QUICK_ACTIONS: { label: string; goal: string }[] = [
  {
    label: "GC content",
    goal: "What is the GC content of ATGCATGCATGCATGC?",
  },
  {
    label: "Reverse-complement + GC",
    goal: "Give me the GC content of the reverse complement of ATGCATGCATGCATGCATGC.",
  },
  {
    label: "CRISPR edit report",
    goal:
      "Run a CRISPR edit report against this target locus and recommend a guide " +
      "(NGG PAM, top 5 candidates, simulate the top 3 outcomes):\n\n" +
      "ATGGCGCCGTTGATCCGTGTCATCCGGAACAACCCGGAGGTTAACAACGGCAACTAACGGTCCAGGTAA",
  },
  {
    label: "ORF finder",
    goal:
      "Find all ORFs of at least 30 amino acids in this sequence (forward strand):\n\n" +
      "ATGGCGCCGTTGATCCGTGTCATCCGGAACAACCCGGAGGTTAACAACGGCAACTAACGGTCCAGGTAA",
  },
  {
    label: "PCR primers",
    goal:
      "Design PCR primers flanking the central 50 nt of this template " +
      "(Tm ~60°C, product 100-200 bp, top 3 pairs):\n\n" +
      "GCAATTCCCAATGGCAAAGGTAAAATCCATCGTAACGTGGAATCCAAATAAGGCATATATATGCAACC" +
      "GATACGTAAGCAGTACCGGTGAACGTGGCTTAATGCCCTTGACATAGCCGTATCAATGGTTCCAAGG" +
      "CTCTAGGTTCGATCGTACCGTACGATACGAATGGCATTTAGCATGAAGTCATAGCCTTAGCATTGCA" +
      "ACTGCATGCAA",
  },
];

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
      <div className="flex flex-wrap gap-1.5">
        {QUICK_ACTIONS.map((action) => (
          <button
            key={action.label}
            type="button"
            onClick={() => setText(action.goal)}
            disabled={disabled}
            className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600 shadow-sm hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            title={action.goal}
          >
            {action.label}
          </button>
        ))}
      </div>
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
        <span>Ctrl/Cmd + Enter to send · click a chip to pre-fill</span>
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
