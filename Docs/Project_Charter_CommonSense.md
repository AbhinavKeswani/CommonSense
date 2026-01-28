# **Project Charter: CommonSense**

**Subtitle:** Localized Financial Intelligence & Portfolio Optimization Engine

## **1\. Project Vision**

**CommonSense** is a private, automated financial intelligence pipeline. It leverages localized AI to strip away the complexity of SEC filings, providing "Common-Sized" and "Flux" analysis that helps investors make sense of raw data. By hosting the brain of the project locally, we ensure data privacy and zero API overhead.

## **2\. Core Architecture Layers**

### **Layer 1: The SEC Sentinel**

* **Role:** Continuous listener for the SEC’s Public Document Feed (EDGAR).  
* **Tech:** edgartools \+ Python RSS Polling.  
* **Output:** Structured financial tables saved as Parquet files.

### **Layer 2: The Ticker Intelligence Hub**

* **Role:** Manual research and historical backfilling.  
* **Feature:** Allows for targeted deep-dives and sector-relative benchmarking.

### **Layer 3: The Sentiment Pulse**

* **Role:** News and RSS aggregation to provide qualitative context.  
* **Scoring:** Custom financial sentiment modeling (e.g., scoring impact vs. volume).

### **Layer 4: The Sovereign LLM Layer**

* **Role:** The "Analyst" logic.  
* **Hosting:** Distributed between Desktop (RTX 3080\) for speed and M3 Max for high-memory tasks.  
* **Logic:** RAG-based analysis explaining financial variances (Flux Analysis).

## **3\. Analytical Methodologies**

### **I. Common-Sized Analysis ("The Common")**

* **Vertical Analysis:** Expresses financial line items as percentages of Total Revenue (Income Statement) or Total Assets (Balance Sheet).  
* **Goal:** Enables apples-to-apples comparison across different company sizes.

### **II. Flux Analysis ("The Sense")**

* **Horizontal Analysis:** Tracks period-over-period changes in "cents" to make "sense" of growth or decline.  
* **Thresholds:** Automated flagging of material changes (\>10% variance).

## **4\. Technical Roadmap**

* **Database:** SQLite (Metadata) & Parquet (Financials).  
* **AI:** Ollama for local inference; ChromaDB for vector storage.  
* **Network:** Tailscale for private, remote access between M3 Max and Desktop.  
* **Portfolio:** PyPortfolioOpt for translating AI sentiment into asset weights.