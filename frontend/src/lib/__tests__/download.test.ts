/**
 * Tests for the export helpers. The pure serializers (toCsv, svgToString) get the
 * thorough coverage; downloadBlob is exercised against stubbed object-URL + anchor so the
 * "trigger a download and always revoke the URL" contract is verified without a real save.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { downloadBlob, svgToString, toCsv } from "../download";

describe("toCsv", () => {
  it("joins cells with commas and rows with CRLF", () => {
    expect(toCsv([["a", "b"], [1, 2]])).toBe("a,b\r\n1,2");
  });

  it("renders null/undefined as empty cells, booleans/numbers as text", () => {
    expect(toCsv([["x", null, undefined, 0, false]])).toBe("x,,,0,false");
  });

  it("quotes fields containing a comma, quote, or newline (RFC 4180), doubling inner quotes", () => {
    expect(toCsv([["x,y", 'a"b', "line\nbreak"]])).toBe('"x,y","a""b","line\nbreak"');
  });
});

describe("svgToString", () => {
  it("serializes an SVG element with the xmlns and its children", () => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", "1");
    svg.appendChild(rect);
    document.body.appendChild(svg);

    const out = svgToString(svg as SVGSVGElement);
    expect(out).toContain('xmlns="http://www.w3.org/2000/svg"');
    expect(out).toContain("<rect");
    // Inline-attribute fills round-trip untouched.
    expect(out).toContain('x="1"');

    svg.remove();
  });

  it("does not throw when computed styles are unavailable", () => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg") as SVGSVGElement;
    expect(() => svgToString(svg)).not.toThrow();
  });
});

describe("downloadBlob", () => {
  let clickSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    clickSpy = vi.fn();
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:fake");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreate(tag);
      if (tag === "a") el.click = clickSpy;
      return el;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates an object URL, clicks an anchor with the filename, and revokes the URL", () => {
    downloadBlob("guides.csv", "text/csv", "a,b\r\n1,2");
    expect(vi.mocked(URL.createObjectURL)).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(vi.mocked(URL.revokeObjectURL)).toHaveBeenCalledWith("blob:fake");
  });

  it("revokes the URL even if the anchor click throws", () => {
    clickSpy.mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => downloadBlob("x.csv", "text/csv", "x")).toThrow("blocked");
    expect(vi.mocked(URL.revokeObjectURL)).toHaveBeenCalledWith("blob:fake");
  });
});
