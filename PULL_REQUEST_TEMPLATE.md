# Pull Request Template

## 📋 Description

Please provide a clear and concise description of what this PR accomplishes.

### Type of Change
- [ ] 🐛 Bug fix (non-breaking change which fixes an issue)
- [ ] ✨ New feature (non-breaking change which adds functionality)
- [ ] 💥 Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] 📚 Documentation update
- [ ] 🔧 Maintenance/refactoring
- [ ] 🧪 Tests

## 🔍 Testing

### Test Environment
- [ ] Tested with Home Assistant version: ____
- [ ] Tested with Z-Wave JS integration
- [ ] Tested with real Z-Wave lock hardware
- [ ] Tested with template/mock locks

### Testing Checklist
- [ ] All existing functionality continues to work
- [ ] New code has been tested thoroughly
- [ ] No new errors or warnings in Home Assistant logs
- [ ] Frontend panel loads and functions correctly
- [ ] Service calls work as expected
- [ ] Data persistence works across HA restarts

## 📸 Screenshots (if applicable)

Please add screenshots of any UI changes or new features.

## ✅ Code Quality Checklist

- [ ] Code follows the existing style and conventions
- [ ] All new code is properly documented with docstrings
- [ ] Added/updated tests for new functionality
- [ ] Ran `pre-commit run --all-files` successfully
- [ ] No TODO comments left in production code (use GitHub issues instead)
- [ ] Updated CHANGELOG.md with changes
- [ ] Updated documentation if needed

## 🔗 Related Issues

Closes #___
Related to #___

## 📝 Additional Notes

Any additional information, context, or concerns that reviewers should know about.

---

## Reviewer Guidelines

### Code Review Focus Areas

1. **Architecture Compliance**
   - Follows zero sensor pollution pattern
   - Uses object-oriented data storage
   - Maintains backend-driven UI approach

2. **Home Assistant Best Practices**
   - Proper use of coordinators and entities
   - Correct service registration
   - Following integration guidelines

3. **Z-Wave Integration**
   - Proper error handling for Z-Wave failures
   - Safe PIN code management
   - Sync status accuracy

4. **Security Considerations**
   - No hardcoded credentials
   - Safe handling of PIN codes
   - Input validation

### Testing Requirements

- [ ] Test with multiple lock brands if possible
- [ ] Verify friendly name functionality
- [ ] Test time-based access controls
- [ ] Verify parent-child lock synchronization
- [ ] Test error scenarios and recovery