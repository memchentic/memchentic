import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import { AutomationPanel } from "./AutomationPanel";

vi.mock("../../api", () => ({ api: vi.fn() }));

const apiMock = vi.mocked(api);

describe("AutomationPanel", () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockImplementation((path: string, init?: RequestInit) => {
      if (
        path === "/api/system/automation" &&
        (!init || init.method === undefined || init.method === "GET")
      ) {
        return Promise.resolve({
          resolve_supersedes: false,
          resolve_near_dups: false,
          propose_consolidations: false,
          auto_apply_consolidations: false,
        });
      }
      if (path === "/api/system/automation" && init?.method === "PUT") {
        const body = JSON.parse(String(init.body ?? "{}")) as {
          resolve_supersedes?: boolean;
          resolve_near_dups?: boolean;
          propose_consolidations?: boolean;
          auto_apply_consolidations?: boolean;
        };
        return Promise.resolve({
          resolve_supersedes: body.resolve_supersedes ?? false,
          resolve_near_dups: body.resolve_near_dups ?? false,
          propose_consolidations: body.propose_consolidations ?? false,
          auto_apply_consolidations: body.auto_apply_consolidations ?? false,
        });
      }
      if (path === "/api/system/automation/run" && init?.method === "POST") {
        return Promise.resolve({
          conflicts_forgotten: 1,
          near_dups_forgotten: 0,
          consolidations_applied: 0,
        });
      }
      return Promise.reject(new Error(`unexpected ${init?.method ?? "GET"} ${path}`));
    });
  });

  it("asks whether to clear pending when enabling auto-resolve", async () => {
    render(<AutomationPanel />);
    const group = await screen.findByRole("group", { name: "Auto-resolve conflicts" });
    fireEvent.click(within(group).getByRole("button", { name: /Enabled/ }));

    expect(await screen.findByRole("dialog", { name: "Auto-resolve conflicts" })).toBeInTheDocument();
    expect(screen.getByText(/Also forget any pending superseded memories/i)).toBeInTheDocument();
    expect(apiMock).not.toHaveBeenCalledWith(
      "/api/system/automation",
      expect.objectContaining({ method: "PUT" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Enable and clear now" }));
    await waitFor(() =>
      expect(apiMock).toHaveBeenCalledWith(
        "/api/system/automation",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ resolve_supersedes: true }),
        }),
      ),
    );
    await waitFor(() =>
      expect(apiMock).toHaveBeenCalledWith(
        "/api/system/automation/run",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("enables without clearing when the user chooses Enable only", async () => {
    render(<AutomationPanel />);
    const group = await screen.findByRole("group", { name: "Auto-resolve near-duplicates" });
    fireEvent.click(within(group).getByRole("button", { name: /Enabled/ }));

    expect(
      await screen.findByRole("dialog", { name: "Auto-resolve near-duplicates" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Enable only" }));

    await waitFor(() =>
      expect(apiMock).toHaveBeenCalledWith(
        "/api/system/automation",
        expect.objectContaining({
          method: "PUT",
          body: JSON.stringify({ resolve_near_dups: true }),
        }),
      ),
    );
    expect(apiMock).not.toHaveBeenCalledWith(
      "/api/system/automation/run",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
