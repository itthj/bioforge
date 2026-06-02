/**
 * Tests for IgvOfftargetViewer (Slice B).
 *
 * igv.js is mocked so the lazy-load shell + the honesty gate (placeable -> hg38,
 * non-placeable -> table, never a locus) are exercised deterministically.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IgvOfftargetViewer } from "../IgvOfftargetViewer";

const { createBrowserMock, removeBrowserMock } = vi.hoisted(() => ({
  createBrowserMock: vi.fn(),
  removeBrowserMock: vi.fn(),
}));

vi.mock("igv", () => ({
  default: { createBrowser: createBrowserMock, removeBrowser: removeBrowserMock },
}));

beforeEach(() => {
  createBrowserMock.mockReset();
  removeBrowserMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// Raw top_hits dicts, as the backend serializes them.
function placedHit(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    accession: "NC_000001.11",
    organism: "Homo sapiens",
    subject_definition: "chr1",
    mismatch_count: 2,
    mit_score: 0.5,
    cfd_mismatch_score: 0.4,
    risk_label: "high",
    genomic_placement: {
      build: "GRCh38",
      chromosome: "chr1",
      start: 1000,
      end: 1020,
      strand: "+",
      source_accession: "NC_000001.11",
    },
    ...over,
  };
}

function unplacedHit(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    accession: "NM_007294.4",
    organism: "Homo sapiens",
    subject_definition: "BRCA1 mRNA",
    mismatch_count: 0,
    mit_score: 1.0,
    cfd_mismatch_score: null,
    risk_label: "high",
    genomic_placement: null,
    ...over,
  };
}

describe("IgvOfftargetViewer", () => {
  it("offers the hg38 browser and notes how many hits are placeable", () => {
    render(<IgvOfftargetViewer hits={[placedHit(), unplacedHit()]} />);
    expect(
      screen.getByRole("button", { name: /load hg38 browser/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 hit\(s\) sit on a GRCh38 chromosome/i)).toBeInTheDocument();
  });

  it("shows no browser and an honest note when nothing is placeable", () => {
    render(<IgvOfftargetViewer hits={[unplacedHit(), unplacedHit()]} />);
    expect(
      screen.queryByRole("button", { name: /load hg38 browser/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/none of the 2 returned hit\(s\) sit on a GRCh38 primary chromosome/i),
    ).toBeInTheDocument();
  });

  it("always lists non-placeable hits with their accession, never a locus", () => {
    render(<IgvOfftargetViewer hits={[placedHit(), unplacedHit()]} />);
    expect(screen.getByText(/not on hg38/i)).toBeInTheDocument();
    expect(screen.getByText(/NM_007294\.4/)).toBeInTheDocument();
  });

  it("loads igv on the hosted hg38 genome with placed features", async () => {
    createBrowserMock.mockResolvedValue({ dispose: vi.fn() });
    render(<IgvOfftargetViewer hits={[placedHit()]} />);
    await userEvent.click(
      screen.getByRole("button", { name: /load hg38 browser/i }),
    );
    await waitFor(() => expect(screen.getByText(/^Loaded$/)).toBeInTheDocument());

    const config = createBrowserMock.mock.calls[0][1] as {
      genome: string;
      tracks: Array<{ features: unknown[] }>;
    };
    expect(config.genome).toBe("hg38");
    expect(config.tracks[0].features).toHaveLength(1);
  });

  it("degrades gracefully when igv.js fails to initialize", async () => {
    createBrowserMock.mockRejectedValue(new Error("no canvas in this env"));
    render(<IgvOfftargetViewer hits={[placedHit()]} />);
    await userEvent.click(
      screen.getByRole("button", { name: /load hg38 browser/i }),
    );
    await waitFor(() =>
      expect(screen.getByText(/igv\.js failed to render/i)).toBeInTheDocument(),
    );
  });
});
