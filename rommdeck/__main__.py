import sys

if "--apply-pending" in sys.argv:
    from .steam import apply_pending
    apply_pending()
elif "--install" in sys.argv:
    from .installer import main
    main()
else:
    from .app import main
    main()
