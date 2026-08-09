import AgentMarkdownView from "@/components/AgentMarkdownView";

export const dynamic = "force-dynamic";

export default async function TechnicalAnalysisPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  return (
    <AgentMarkdownView
      symbol={symbol}
      endpoint={`/api/symbols/${encodeURIComponent(symbol)}/technical-analysis`}
      resultKey="analysis"
      title={`🔬 ${symbol} Technical Analysis`}
      subtitle="Detailed technical analysis and strategy recommendations."
      emptyText="(No analysis generated)"
    />
  );
}
