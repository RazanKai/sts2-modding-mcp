using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Runs;

namespace MCPTest;

/// <summary>
/// CombatManager.IsPlayPhase was removed from the game API; this restores the
/// old boolean by checking the local player's PlayerCombatState.Phase directly.
/// </summary>
public static class CombatManagerExtensions
{
    public static bool IsPlayPhase(this CombatManager? cm)
    {
        if (cm == null || !cm.IsInProgress) return false;
        RunState? state = RunManager.Instance?.DebugOnlyGetState();
        var me = LocalContext.GetMe(state);
        return me?.PlayerCombatState?.Phase == PlayerTurnPhase.Play;
    }
}
