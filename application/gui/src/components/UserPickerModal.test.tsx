import { faPlus } from "@fortawesome/free-solid-svg-icons";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { UserPickerModal } from "./UserPickerModal";

vi.mock("../api", () => ({ api: vi.fn() }));

const apiMock = vi.mocked(api);

const USERS = [
  { id: "u1", email: "member@example.test", display_name: null },
  { id: "u2", email: "bea@example.test", display_name: "Bea" },
  { id: "u3", email: "cal@example.test", display_name: null },
];

function renderPicker(props: Partial<Parameters<typeof UserPickerModal>[0]> = {}) {
  const addUser = vi.fn<(userId: string) => Promise<void>>(() => Promise.resolve());
  const onAdded = vi.fn();
  const onClose = vi.fn();
  render(
    <UserPickerModal
      open
      titleId="picker-title"
      excludeIds={new Set(["u1"])}
      addIcon={faPlus}
      addUser={addUser}
      onAdded={onAdded}
      onClose={onClose}
      {...props}
    />,
  );
  return { addUser, onAdded, onClose };
}

describe("UserPickerModal", () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockImplementation((path: string) =>
      path === "/api/users" ? Promise.resolve(USERS) : Promise.reject(new Error("not found")),
    );
  });

  it("lists directory users except the excluded ones", async () => {
    renderPicker();
    expect(await screen.findByLabelText("Add bea@example.test")).toBeInTheDocument();
    expect(screen.getByLabelText("Add cal@example.test")).toBeInTheDocument();
    expect(screen.queryByLabelText("Add member@example.test")).toBeNull();
    expect(screen.getByRole("searchbox", { name: "Search users" })).toBeInTheDocument();
  });

  it("adds every selected user through addUser, then closes", async () => {
    const { addUser, onAdded, onClose } = renderPicker();
    fireEvent.click(await screen.findByLabelText("Add bea@example.test"));
    fireEvent.click(screen.getByLabelText("Add cal@example.test"));
    fireEvent.click(screen.getByLabelText("Done (2)"));
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(addUser.mock.calls.map((call) => call[0])).toEqual(["u2", "u3"]);
    expect(onAdded).toHaveBeenCalledTimes(1);
  });

  it("shows the error of a failed add and keeps the dialog open", async () => {
    const addUser = vi.fn<(userId: string) => Promise<void>>(() => Promise.reject(new Error("forbidden")));
    const { onAdded, onClose } = renderPicker({ addUser });
    fireEvent.click(await screen.findByLabelText("Add bea@example.test"));
    fireEvent.click(screen.getByLabelText("Done (1)"));
    expect(await screen.findByText("forbidden")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(onAdded).not.toHaveBeenCalled();
  });

  it("offers an email invitation only when inviteByEmail is given", async () => {
    renderPicker();
    const search = await screen.findByPlaceholderText("Search users…");
    fireEvent.change(search, { target: { value: "new@example.test" } });
    expect(screen.queryByLabelText("Invite new@example.test")).toBeNull();
  });

  it("invites an email address through inviteByEmail", async () => {
    const inviteByEmail = vi.fn(() => Promise.resolve());
    const { onAdded } = renderPicker({ inviteByEmail });
    const search = await screen.findByPlaceholderText("Search users…");
    fireEvent.change(search, { target: { value: "new@example.test" } });
    fireEvent.click(screen.getByLabelText("Invite new@example.test"));
    await waitFor(() => expect(inviteByEmail).toHaveBeenCalledWith("new@example.test"));
    await waitFor(() => expect(onAdded).toHaveBeenCalledTimes(1));
  });
});
