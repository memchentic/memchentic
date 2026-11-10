import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Modal } from "./overlay";

function Harness({ onClose = () => {} }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <Modal
        open={open}
        labelledBy="harness-title"
        onClose={() => {
          onClose();
          setOpen(false);
        }}
      >
        <h3 id="harness-title">Harness</h3>
        <button type="button">First</button>
        <button type="button">Last</button>
      </Modal>
    </>
  );
}

describe("Modal", () => {
  it("puts the dialog role on the panel and focuses it on open", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("Open"));
    const dialog = screen.getByRole("dialog", { name: "Harness" });
    expect(dialog).toHaveClass("overlay-panel");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(document.activeElement).toBe(dialog);
  });

  it("keeps Tab inside the panel", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("Open"));
    const first = screen.getByText("First");
    const last = screen.getByText("Last");
    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it("closes on Escape and returns focus to the opener", () => {
    vi.useFakeTimers();
    try {
      const onClose = vi.fn();
      render(<Harness onClose={onClose} />);
      const opener = screen.getByText("Open");
      opener.focus();
      fireEvent.click(opener);
      expect(document.activeElement).not.toBe(opener);
      fireEvent.keyDown(document, { key: "Escape" });
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(document.activeElement).toBe(opener);
      act(() => {
        vi.runAllTimers();
      });
      expect(screen.queryByRole("dialog")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
