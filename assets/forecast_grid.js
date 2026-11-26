window.dashAgGridFunctions = window.dashAgGridFunctions || {};

window.dashAgGridFunctions.compareForecastDates = function (left, right) {
    const dateKey = (value) => {
        const match = /^(\d{2})\.(\d{2})\.(\d{4})(.*)$/.exec(value || "");
        return match ? match[3] + match[2] + match[1] + match[4] : "";
    };
    return dateKey(left).localeCompare(dateKey(right));
};
