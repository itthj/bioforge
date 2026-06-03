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
  {
    label: "BRCA1 structure",
    goal:
      "Fetch the AlphaFold structure for BRCA1 (UniProt P38398) and summarize " +
      "where the prediction is confident vs uncertain.",
  },
  {
    label: "Hemoglobin (4HHB)",
    goal:
      "Fetch the experimental hemoglobin structure (PDB 4HHB) and describe " +
      "its chains, cofactors, and resolution. What can the B-factors tell us " +
      "about flexibility?",
  },
  {
    label: "Best p53 structure",
    goal:
      "Find the best available 3D structure of TP53 (UniProt P04637) — choose " +
      "automatically between an experimental structure and an AlphaFold prediction, " +
      "and explain the trade-off in your answer.",
  },
  {
    label: "BRCA1 domains",
    goal:
      "Fetch the InterPro domain annotations for BRCA1 (UniProt P38398) and " +
      "describe the major functional regions of the protein.",
  },
  {
    label: "Compare BRCA1 structures",
    goal:
      "Compare the experimental and predicted 3D structures of BRCA1 (UniProt " +
      "P38398). Tell me where the prediction is experimentally validated and " +
      "where it's the only available model.",
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
            className="rounded-full border border-border bg-surface px-2.5 py-1 text-[11px] font-medium text-fg-muted shadow-sm transition-colors hover:border-accent hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
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
        className="w-full rounded-md border border-border bg-surface p-3 font-mono text-sm text-fg shadow-sm placeholder:text-fg-subtle focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-60"
      />
      <div className="flex items-center justify-between text-xs text-fg-subtle">
        <span>Ctrl/Cmd + Enter to send · click a chip to pre-fill</span>
        <button
          type="submit"
          disabled={disabled || !text.trim()}
          className="rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-accent-fg shadow-sm transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {disabled ? "Running…" : "Send"}
        </button>
      </div>
    </form>
  );
}
