"""Entry point for `python -m aw_coach` (background service mode)."""



def main():
    from aw_coach.scheduler import run_scheduler
    run_scheduler()


if __name__ == "__main__":
    main()
