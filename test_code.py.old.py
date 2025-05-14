if (0):
    # Test the humanize_timestamp function
    """
    10s ago
    1m ago
    today
    21h ago
    21h ago
    1d ago
    2d ago
    12d ago
    3M ago
    3y ago
    """
    print(todoist_trmnl.humanize_timestamp(datetime.now().timestamp() - 10, tiny=True))
    print(todoist_trmnl.humanize_timestamp(datetime.now().timestamp() - 60, tiny=True))
    print(todoist_trmnl.humanize_timestamp(1747177200, tiny=True))
    print(todoist_trmnl.humanize_timestamp(1747177100, tiny=True))
    print(todoist_trmnl.humanize_timestamp(1747176100, tiny=True))
    print(todoist_trmnl.humanize_timestamp(1747167100, tiny=True))
    print(todoist_trmnl.humanize_timestamp(1747077100, tiny=True))
    print(todoist_trmnl.humanize_timestamp(1746177100, tiny=True))
    print(todoist_trmnl.humanize_timestamp(1737177100, tiny=True))
    print(todoist_trmnl.humanize_timestamp(1647177100, tiny=True))
    quit()