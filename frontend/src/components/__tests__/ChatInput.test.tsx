/**
 * Tests for ChatInput.
 *
 * Covers the contract the rest of the app depends on:
 *   - Submitting calls onSubmit with the trimmed text and clears the box
 *   - Empty / whitespace-only text never triggers onSubmit
 *   - disabled prop disables both the button and the chips
 *   - Quick-action chips pre-fill the textarea without auto-submitting
 *   - Ctrl/Cmd+Enter submits from inside the textarea
 *
 * Deliberately NOT testing visual styling — Tailwind classes change often and
 * snapshot tests on them produce noise without catching real bugs.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatInput } from "../ChatInput";

describe("ChatInput", () => {
  it("calls onSubmit with the trimmed text and clears the textarea", async () => {
    const user = userEvent.setup();
    const handle = vi.fn();
    render(<ChatInput onSubmit={handle} />);

    const textarea = screen.getByPlaceholderText(/What do you want BioForge to do/i);
    await user.type(textarea, "   GC content of ATGC   ");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(handle).toHaveBeenCalledTimes(1);
    expect(handle).toHaveBeenCalledWith("GC content of ATGC");
    expect(textarea).toHaveValue("");
  });

  it("does not call onSubmit when the textarea is empty", async () => {
    const user = userEvent.setup();
    const handle = vi.fn();
    render(<ChatInput onSubmit={handle} />);

    const button = screen.getByRole("button", { name: /send/i });
    // The button is disabled while empty — clicking is a no-op but we also assert it.
    expect(button).toBeDisabled();
    await user.click(button);
    expect(handle).not.toHaveBeenCalled();
  });

  it("does not call onSubmit when text is whitespace-only", async () => {
    const user = userEvent.setup();
    const handle = vi.fn();
    render(<ChatInput onSubmit={handle} />);

    const textarea = screen.getByPlaceholderText(/What do you want BioForge to do/i);
    await user.type(textarea, "   ");
    // Whitespace still keeps the button disabled because we trim before checking.
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
    expect(handle).not.toHaveBeenCalled();
  });

  it("disables the textarea, button, and chips when `disabled` is true", () => {
    render(<ChatInput onSubmit={vi.fn()} disabled />);

    expect(screen.getByPlaceholderText(/What do you want BioForge to do/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /running/i })).toBeDisabled();
    // All quick-action chip buttons should also be disabled.
    const chips = screen.getAllByRole("button", { name: /(GC content|CRISPR edit report|ORF finder)/i });
    for (const chip of chips) {
      expect(chip).toBeDisabled();
    }
  });

  it("pre-fills the textarea when a quick-action chip is clicked (does NOT auto-submit)", async () => {
    const user = userEvent.setup();
    const handle = vi.fn();
    render(<ChatInput onSubmit={handle} />);

    await user.click(screen.getByRole("button", { name: /CRISPR edit report/i }));

    const textarea = screen.getByPlaceholderText(
      /What do you want BioForge to do/i,
    ) as HTMLTextAreaElement;
    expect(textarea.value).toMatch(/CRISPR edit report/i);
    expect(textarea.value.length).toBeGreaterThan(20);
    // Crucial: the chip pre-fills only — the user reviews + edits before sending.
    expect(handle).not.toHaveBeenCalled();
  });

  it("submits on Ctrl+Enter from inside the textarea", () => {
    const handle = vi.fn();
    render(<ChatInput onSubmit={handle} />);

    const textarea = screen.getByPlaceholderText(/What do you want BioForge to do/i);
    fireEvent.change(textarea, { target: { value: "translate ATGAAA" } });
    fireEvent.keyDown(textarea, { key: "Enter", ctrlKey: true });

    expect(handle).toHaveBeenCalledWith("translate ATGAAA");
  });
});
