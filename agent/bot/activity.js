let _current = 'idle'

function setActivity(name) {
    _current = name
}

function getActivity() {
    return _current
}

module.exports = { setActivity, getActivity }
