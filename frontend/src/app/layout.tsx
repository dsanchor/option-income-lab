import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "@/lib/query-client";
import { TopNav } from "@/components/TopNav";
import { Toaster } from "sonner";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Option Income Lab",
  description: "Where boring dividends get interesting — covered calls, cash-secured puts, and AI-powered monitoring.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col bg-bg text-text">
        <Providers>
          <TopNav />
          <main className="mx-auto w-full max-w-[1200px] px-6 py-6">{children}</main>
          <Toaster
            theme="dark"
            position="top-right"
            richColors
            closeButton
            toastOptions={{
              style: {
                background: "var(--bg-card)",
                border: "1px solid var(--border)",
                color: "var(--text)",
                borderRadius: "var(--radius)",
              },
            }}
          />
        </Providers>
      </body>
    </html>
  );
}
