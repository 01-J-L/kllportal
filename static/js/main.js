document.addEventListener('DOMContentLoaded', () => {
  // Live Client-side search for accordion & card items
  const searchInput = document.getElementById('portalLiveSearch');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      const term = e.target.value.toLowerCase().trim();
      const searchableItems = document.querySelectorAll('.searchable-item');

      searchableItems.forEach((item) => {
        const text = item.textContent.toLowerCase();
        if (text.includes(term)) {
          item.style.display = '';
        } else {
          item.style.display = 'none';
        }
      });
    });
  }
});