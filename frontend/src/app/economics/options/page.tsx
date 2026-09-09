import EconomicsView from "@/components/EconomicsView";

export const metadata = { title: "Economics Options — Portfolio Income Lab" };

export default function EconomicsOptionsPage() {
  return (
    <EconomicsView
      basePath="/economics/options"
      title="Economics · Options"
      description="Premium, buybacks, net income, and options RoC analytics across all symbols."
    />
  );
}
