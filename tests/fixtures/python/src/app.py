def greeting(name):
    extra_value = 42  # Deliberate finding for the analysis/export smoke test.
    if name:
        return "Hello " + name
    return "Hello world"
