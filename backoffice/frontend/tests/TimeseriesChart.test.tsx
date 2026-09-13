import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TimeseriesChart } from "../src/components/TimeseriesChart";

describe("TimeseriesChart", () => {
  const points = [
    { date: "2026-08-27", value: 10 },
    { date: "2026-08-28", value: 0 },
    { date: "2026-08-29", value: 20 },
  ];

  it("renders a short date label for every point", () => {
    render(<TimeseriesChart points={points} label="Sessions per day" />);
    expect(screen.getByText("08/27")).toBeInTheDocument();
    expect(screen.getByText("08/28")).toBeInTheDocument();
    expect(screen.getByText("08/29")).toBeInTheDocument();
  });

  it("uses one color for every bar — height is the only magnitude encoding (dataviz anti-pattern check)", () => {
    const { container } = render(<TimeseriesChart points={points} label="Sessions per day" />);
    const bars = container.querySelectorAll('div[style*="var(--series-1)"]');
    expect(bars).toHaveLength(points.length);
  });

  it("scales bar height proportionally to the largest point", () => {
    const { container } = render(<TimeseriesChart points={points} label="Sessions per day" />);
    const bars = Array.from(
      container.querySelectorAll('div[style*="var(--series-1)"]'),
    ) as HTMLDivElement[];
    expect(bars[0].style.height).toBe("50%");
    expect(bars[1].style.height).toBe("0%");
    expect(bars[2].style.height).toBe("100%");
  });

  it("handles an all-zero range without dividing by zero", () => {
    render(<TimeseriesChart points={[{ date: "2026-08-27", value: 0 }]} label="Sessions per day" />);
    expect(screen.getByText("08/27")).toBeInTheDocument();
  });

  it("shows a message instead of an empty chart when there are no points", () => {
    render(<TimeseriesChart points={[]} label="Sessions per day" />);
    expect(screen.getByText(/no data/i)).toBeInTheDocument();
  });
});
