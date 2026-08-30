import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatTile } from "../src/components/StatTile";

describe("StatTile", () => {
  it("renders the label and value", () => {
    render(<StatTile label="Sessions" value="42" />);
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });
});
