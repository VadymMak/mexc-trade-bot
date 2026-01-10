from exploration import exploration_manager, get_params_for_trade

print("=" * 60)
print("TESTING EXPLORATION MODE")
print("=" * 60)

# Симулируем 10 сделок
for i in range(10):
    params, is_exploration = get_params_for_trade('TEST', {
        'take_profit_bps': 2.0,
        'stop_loss_bps': -2.0,
        'trailing_stop_enabled': True,
        'trail_activation_bps': 1.8,
        'trail_distance_bps': 0.5,
        'timeout_seconds': 40.0
    })
    
    mode = 'EXPLORE' if is_exploration else 'EXPLOIT'
    print(f'Trade {i+1:2d}: {mode:7s} | TP={params["take_profit_bps"]:5.1f} | SL={params["stop_loss_bps"]:6.1f} | Timeout={params["timeout_seconds"]:4.0f}s')

print()
print("=" * 60)
stats = exploration_manager.get_stats()
print(f"Total:       {stats['total']}")
print(f"Exploration: {stats['exploration']} ({stats['exploration_rate']:.1%})")
print(f"Exploitation: {stats['exploitation']} ({1-stats['exploration_rate']:.1%})")
print("=" * 60)