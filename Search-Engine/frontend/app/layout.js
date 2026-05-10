import "./globals.css";

export const metadata = {
  title: "HPE GreenLake Search",
  description: "Search experience for object storage",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
