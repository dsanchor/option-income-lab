import BestOptionsView from "@/components/BestOptionsView";

export const dynamic = "force-dynamic";

export default async function BestOptionsPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  return <BestOptionsView symbol={symbol} />;
}
