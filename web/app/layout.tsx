import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Providers } from "@/components/providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Rare Disease Atlas",
  description:
    "Research navigation across rare-disease mechanisms, evidence and communities. Not medical advice.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <Providers>
          <header className="border-b border-dust bg-white">
            <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-5 py-4 sm:px-8">
              <Link href="/" className="flex items-center gap-3 text-sm font-semibold tracking-tight text-pine">
                <span className="flex size-8 items-center justify-center rounded-lg bg-pine text-base text-white">✳</span>
                Rare Disease Atlas
              </Link>
              <nav className="flex items-center gap-4 text-xs font-medium text-muted-foreground sm:text-sm">
                <Link href="/" className="hover:text-hunter">Explore</Link>
                <Link href="/mechanism" className="rounded-full border border-dust px-3 py-1.5 text-pine transition-colors hover:bg-dust/40">
                  Mechanism finder
                </Link>
              </nav>
            </div>
          </header>
          <main className="mx-auto w-full max-w-[1600px] flex-1 px-5 py-7 sm:px-8 lg:py-9">{children}</main>
          <footer className="border-t border-dust/70">
            <p className="mx-auto max-w-[1600px] px-5 py-5 text-center text-xs text-muted-foreground">
              Research navigation, not medical advice.
            </p>
          </footer>
        </Providers>
      </body>
    </html>
  );
}
