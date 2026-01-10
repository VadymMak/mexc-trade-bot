"""
Find LINKUSDT-like symbols using Scanner API
Filters for mid-cap altcoins with good liquidity and tight spreads
"""
import asyncio
import httpx
from typing import List, Dict

BASE_URL = "http://localhost:8000"

async def find_linkusdt_like_symbols() -> List[Dict]:
    """
    Find symbols similar to LINKUSDT characteristics:
    - Mid price ($5-50)
    - Tight spread (<5 bps)
    - Good liquidity (depth >$5K)
    - High volume (>$50K/24h)
    """
    
    print("=" * 70)
    print("FINDING LINKUSDT-LIKE SYMBOLS")
    print("=" * 70)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Query scanner
        params = {
            'exchange': 'gate',  # or 'mexc' or 'all'
            'quote': 'USDT',
            'limit': 100,
            'max_spread_bps': 5.0,       # Tight spread like LINK
            'min_quote_vol_usd': 50000,  # Good volume
            'min_depth5_usd': 5000,      # Good depth at 5 bps
            'min_usdpm': 2000,           # Active trading
            'min_trades_per_min': 10,    # Many trades
            'exclude_leveraged': True,
            'include_stables': False,
            'liquidity_test': True,      # Only grade B or better
        }
        
        try:
            response = await client.get(
                f"{BASE_URL}/api/scanner/top",
                params=params
            )
            response.raise_for_status()
            results = response.json()
            
            print(f"✅ Scanner returned {len(results)} candidates")
            
            # Filter by price range (mid-cap like LINK)
            filtered = []
            for sym in results:
                price = sym.get('last', 0)
                symbol = sym.get('symbol', '')
                
                # Skip if price not in mid-cap range
                if price < 0.10 or price > 100:
                    continue
                
                # Skip specific bad actors if known
                if symbol in ['TRXUSDT', 'ATOMUSDT']:
                    continue
                
                filtered.append(sym)
            
            print(f"✅ Filtered to {len(filtered)} mid-cap candidates")
            
            # Sort by score
            filtered.sort(key=lambda x: x.get('score', 0), reverse=True)
            
            return filtered
            
        except Exception as e:
            print(f"❌ Scanner API error: {e}")
            return []

async def main():
    candidates = await find_linkusdt_like_symbols()
    
    if not candidates:
        print("❌ No candidates found")
        return
    
    print("\n" + "=" * 70)
    print("TOP CANDIDATES (LINKUSDT-LIKE)")
    print("=" * 70)
    
    # Show top 20
    print(f"\n{'#':<3} {'SYMBOL':<12} {'PRICE':<10} {'SPREAD':<8} {'DEPTH5':<10} {'$/MIN':<8} {'SCORE':<6}")
    print("-" * 70)
    
    for i, sym in enumerate(candidates[:20], 1):
        symbol = sym.get('symbol', 'N/A')
        last = sym.get('last', 0)
        spread_bps = sym.get('spread_bps', 0)
        depth5_bid = sym.get('depth5_bid_usd', 0)
        depth5_ask = sym.get('depth5_ask_usd', 0)
        depth5_min = min(depth5_bid, depth5_ask) if depth5_bid and depth5_ask else 0
        usdpm = sym.get('usd_per_min', 0)
        score = sym.get('score', 0)
        
        print(
            f"{i:<3} {symbol:<12} ${last:<9.4f} {spread_bps:<8.2f} "
            f"${depth5_min:<9.0f} ${usdpm:<7.0f} {score:<6.1f}"
        )
    
    # Recommended symbols
    print("\n" + "=" * 70)
    print("RECOMMENDED SYMBOLS FOR .ENV")
    print("=" * 70)
    
    top_15 = candidates[:15]
    symbols_str = ','.join([s['symbol'] for s in top_15])
    
    print("\n# Replace in .env:")
    print(f"SYMBOLS={symbols_str}")
    
    print("\n# Or copy/paste:")
    for sym in top_15:
        print(f"  {sym['symbol']}")
    
    # Comparison with LINKUSDT
    print("\n" + "=" * 70)
    print("COMPARISON WITH LINKUSDT")
    print("=" * 70)
    
    linkusdt_ref = {
        'spread_bps': 2.5,
        'depth5_usd': 8000,
        'usdpm': 5000,
        'price': 17.50,
    }
    
    print(f"\nLINKUSDT (reference):")
    print(f"  Spread: {linkusdt_ref['spread_bps']:.1f} bps")
    print(f"  Depth5: ${linkusdt_ref['depth5_usd']:,.0f}")
    print(f"  $/min:  ${linkusdt_ref['usdpm']:,.0f}")
    print(f"  Price:  ${linkusdt_ref['price']:.2f}")
    
    # Show similarity scores for top 5
    print(f"\nTop 5 similarity to LINKUSDT:")
    for i, sym in enumerate(top_15[:5], 1):
        spread_diff = abs(sym.get('spread_bps', 10) - linkusdt_ref['spread_bps'])
        depth_ratio = sym.get('depth5_bid_usd', 0) / linkusdt_ref['depth5_usd']
        usdpm_ratio = sym.get('usd_per_min', 0) / linkusdt_ref['usdpm']
        
        # Simple similarity score
        similarity = (
            (1 - min(spread_diff / 10, 1)) * 0.4 +  # Spread weight
            min(depth_ratio, 1) * 0.3 +              # Depth weight
            min(usdpm_ratio, 1) * 0.3                # Volume weight
        )
        
        print(f"  {i}. {sym['symbol']:<12} similarity: {similarity:.2%}")
    
    print("\n" + "=" * 70)
    print("✅ Analysis complete!")
    print("=" * 70)
    print("\nNext steps:")
    print("  1. Review top 15 symbols")
    print("  2. Update .env with new SYMBOLS list")
    print("  3. Restart backend")
    print("  4. Monitor for 3 days")

if __name__ == "__main__":
    asyncio.run(main())