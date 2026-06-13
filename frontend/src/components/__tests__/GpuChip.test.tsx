/**
 * Tests for GpuChip -- the honest cloud-GPU capability indicator (Limitation #3).
 * api/gpu is mocked; we assert it shows "off" when no backend is configured and the host when one is.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GpuChip } from "../GpuChip";
import * as api from "../../api/gpu";

vi.mock("../../api/gpu");
const mockApi = vi.mocked(api);

beforeEach(() => vi.clearAllMocks());

describe("GpuChip", () => {
  it("shows 'GPU: off' when no backend is configured", async () => {
    mockApi.fetchGpuStatus.mockResolvedValue({ backend: "none", configured: false, endpoint_host: "" });
    render(<GpuChip />);
    expect(await screen.findByText("GPU: off")).toBeInTheDocument();
  });

  it("shows the endpoint host when an HTTP backend is configured", async () => {
    mockApi.fetchGpuStatus.mockResolvedValue({
      backend: "http",
      configured: true,
      endpoint_host: "gpu.example.com",
    });
    render(<GpuChip />);
    expect(await screen.findByText("GPU: gpu.example.com")).toBeInTheDocument();
  });

  it("renders nothing if the status fetch fails", async () => {
    mockApi.fetchGpuStatus.mockRejectedValue(new Error("network"));
    const { container } = render(<GpuChip />);
    expect(container).toBeEmptyDOMElement();
  });
});
