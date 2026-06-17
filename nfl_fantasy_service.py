"""Fantasy football helpers kept separate from betting/parlay logic."""


def show_fantasy_menu():
    print("\nFantasy Football Tools")
    print("1. Rankings")
    print("2. Start/Sit Helper")
    print("3. Waiver Suggestions")
    print("4. Player Projection Comparison")
    choice = input("Select fantasy option 1, 2, 3, or 4: ").strip()
    if choice == "1":
        print("Placeholder rankings: connect fantasy scoring settings, depth charts, injuries, and projections.")
    elif choice == "2":
        print("Placeholder start/sit helper: compare player projections, matchup, and floor/ceiling.")
    elif choice == "3":
        print("Placeholder waivers: identify available players with role/projection gains.")
    elif choice == "4":
        print("Placeholder comparison: show side-by-side weekly projection inputs.")
    else:
        print("Invalid fantasy option.")
