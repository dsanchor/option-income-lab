import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { Providers } from "@/lib/query-client";
import { TopNav } from "@/components/TopNav";
import { Toaster } from "sonner";

const inter = localFont({
  src: "./fonts/inter-latin-wght-normal.woff2",
  variable: "--font-inter",
  weight: "400 700",
  style: "normal",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Portfolio Income Lab",
  description: "DGI, Dividends & Options — AI-powered income portfolio monitoring with covered calls, cash-secured puts, and dividend growth investing.",
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
