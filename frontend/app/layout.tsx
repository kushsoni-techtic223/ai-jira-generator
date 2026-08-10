import type { Metadata } from "next";
import type { CSSProperties, ReactNode } from "react";
import { Syne, Manrope } from "next/font/google";
import "./globals.css";

const syne = Syne({
  subsets: ["latin"],
  variable: "--font-syne",
  display: "swap",
});

const manrope = Manrope({
  subsets: ["latin"],
  variable: "--font-manrope",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Daily Time Logger",
  description:
    "Log today’s work from Jira timers or GitHub commits — and generate backlog tickets from docs.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  const bodyStyle = {
    ["--font-display"]: "var(--font-syne)",
    ["--font-body"]: "var(--font-manrope)",
  } as CSSProperties;

  return (
    <html lang="en" className={`${syne.variable} ${manrope.variable}`}>
      <body className="antialiased" style={bodyStyle}>
        {children}
      </body>
    </html>
  );
}
