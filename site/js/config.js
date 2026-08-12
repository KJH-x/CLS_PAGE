// config.js — Site-wide configuration
// Change R2_PUBLIC_URL to your R2 bucket public URL before deploying.

window.ARCHIVE_CONFIG = {
  R2_PUBLIC_URL: "https://cls.r2.nsapi.top",
  ACCOUNTS: {
    ak: {
      label: "朝陇山",
      index: "/site/index.json",
      search: "/site/search-index.json",
      figures: "/site/figures-index.json",
      figuresSearch: "/site/figures-search-index.json",
    },
    ef: {
      label: "山团团",
      index: "/endfield/site/index.json",
      search: "/endfield/site/search-index.json",
      figures: "/endfield/site/figures-index.json",
      figuresSearch: "/endfield/site/figures-search-index.json",
    },
  },
};
