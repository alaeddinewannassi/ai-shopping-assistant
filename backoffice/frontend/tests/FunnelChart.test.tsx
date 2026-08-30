import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FunnelChart } from "../src/components/FunnelChart";

describe("FunnelChart", () => {
  const stages = [
    { label: "Sessions", value: 100 },
    { label: "Discovery", value: 80 },
    { label: "Ordered", value: 10 },
  ];

  it("renders every stage label and value", () => {
    render(<FunnelChart stages={stages} />);
    for (const stage of stages) {
      expect(screen.getByText(stage.label)).toBeInTheDocument();
    }
    expect(screen.getByText(/100 \(100%\)/)).toBeInTheDocument();
    expect(screen.getByText(/10 \(10%\)/)).toBeInTheDocument();
  });

  it("uses one color for every bar — length is the only magnitude encoding (dataviz anti-pattern check)", () => {
    const { container } = render(<FunnelChart stages={stages} />);
    const bars = container.querySelectorAll('div[style*="var(--series-1)"]');
    expect(bars).toHaveLength(stages.length);
  });

  it("scales bar width proportionally to the largest stage", () => {
    const { container } = render(<FunnelChart stages={stages} />);
    const bars = Array.from(
      container.querySelectorAll('div[style*="var(--series-1)"]'),
    ) as HTMLDivElement[];
    expect(bars[0].style.width).toBe("100%");
    expect(bars[1].style.width).toBe("80%");
    expect(bars[2].style.width).toBe("10%");
  });

  it("handles an all-zero funnel without dividing by zero", () => {
    render(<FunnelChart stages={[{ label: "Sessions", value: 0 }]} />);
    expect(screen.getByText(/0 \(0%\)/)).toBeInTheDocument();
  });
});
